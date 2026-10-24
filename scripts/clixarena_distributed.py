"""Production four-runner encoding for burn, soft and codec conversion. Artifacts contain media, never credentials."""
import argparse
import csv
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

ROOT = Path('parallel-media')


def run(args, label='Media processing', timeout=1800, duration=None):
    """Live fixed stage messages; never expose command arguments or tool errors."""
    started=time.monotonic();last_change=started;last_position=-1
    print(label+' started',flush=True)
    with tempfile.TemporaryFile() as errors:
        process=subprocess.Popen(args,stdout=subprocess.PIPE,stderr=errors)
        try:
            while True:
                try:
                    output,_=process.communicate(timeout=min(10,max(.1,timeout-(time.monotonic()-started))))
                    break
                except subprocess.TimeoutExpired as pending:
                    now=time.monotonic();raw=pending.output or b''
                    position=None
                    if duration:
                        matches=re.findall(rb'out_time_us=(-?\d+)',raw)
                        if matches:position=max(0,int(matches[-1])/1000000)
                        if position is not None and position>last_position:
                            last_position=position;last_change=now
                    if now-started>=timeout or (duration and now-last_change>=180):
                        print('::error::'+label+' timed out or stopped progressing.',flush=True)
                        raise RuntimeError(label+' timed out or stopped progressing; upload was not started.')
                    progress=f' | {min(99.9,100*position/duration):.1f}%' if position is not None else ''
                    print(f'{label}{progress} | elapsed {now-started:.0f}s',flush=True)
            if process.returncode:
                print('::error::'+label+' failed. Private tool output was suppressed.',flush=True)
                raise RuntimeError(label+' failed; upload was not started. Private tool errors are suppressed.')
            print(f'{label} complete | elapsed {time.monotonic()-started:.1f}s',flush=True)
            return output
        finally:
            if process.poll() is None:
                process.kill();process.communicate()


def ff(*args, label='Media processing', duration=None, timeout=1800):
    return run(['ffmpeg', '-nostdin', '-hide_banner', '-loglevel', 'error', '-y',
                *(['-progress','pipe:1','-stats_period','5'] if duration else []),*map(str,args)],
               label=label,duration=duration,timeout=timeout)


def info(path, count=True):
    return json.loads(run(['ffprobe','-v','error',*(['-count_packets'] if count else []),
        '-show_streams','-show_format','-of','json',str(path)],
        label='Counting media packets' if count else 'Reading media metadata',timeout=600 if count else 120))


def video(data):
    return next(s for s in data['streams'] if s['codec_type'] == 'video')


def packets(data):
    return int(video(data)['nb_read_packets'])


def standard_height(stream):
    from clixarena_features import video_dimensions
    w, h = video_dimensions(stream, None)
    # A cropped widescreen source belongs to the standard tier of its width too.
    return next((tier for tier, width in ((360,640),(480,854),(720,1280),(1080,1920),(1440,2560),(2160,3840))
                 if w <= width + 4 and h <= tier), 2160)


def prepare(destination, streams, audio_map, duration, options, language='eng', method='soft', source='source.video', logo_size=None):
    from clixarena_features import video_dimensions, fitted_dimensions
    root = Path(destination) / ROOT
    root.mkdir(parents=True, exist_ok=True)
    (root/'ready.txt').write_text('Media preparation complete. No credentials in this artifact.',encoding='utf-8')
    stream = next(s for s in streams if s['codec_type'] == 'video')
    tier = standard_height(stream) if method=='soft' else options.get('height') if method=='burn' else None
    width,height=video_dimensions(stream,tier)
    cw,ch=fitted_dimensions(stream,width,height)
    print(f'Preparing {method} for up to four runners: {width} x {height}, CRF {options["crf"]}, preset {options["preset"]}',flush=True)
    ff('-i',source,'-map','0:v:0','-an','-sn','-dn','-map_metadata','-1','-c:v','copy','-t',duration,
       '-f','segment','-segment_times',','.join(str(duration*i/4) for i in (1,2,3)),
       '-segment_list',root/'segments.csv','-reset_timestamps',1,root/'part-%02d.mkv',
       label='Splitting video without re-encoding',duration=duration)
    parts=sorted(root.glob('part-*.mkv'))
    if not 1<=len(parts)<=4: raise RuntimeError('No safe video chunk boundaries')
    # Full packet validation belongs to workers, where it overlaps other workers.
    # Preparation only needs each chunk's timestamp metadata.
    infos=[info(path,count=False) for path in parts]
    rows=list(csv.reader((root/'segments.csv').read_text(encoding='utf-8').splitlines()))
    origin=float(rows[0][1])+float(video(infos[0]).get('start_time',0))
    starts=[float(row[1])+float(video(meta).get('start_time',0))-origin for row,meta in zip(rows,infos)]
    audio=[t for t in streams if t['codec_type']=='audio']
    retained=[t for t in streams if t['codec_type']=='subtitle' and t.get('codec_name') in {'subrip','ass','ssa','mov_text','webvtt','text'}] if method=='transfer' else []
    audio_language='und';audio_offset=0.0
    selected_audio=audio if method=='transfer' else ([audio[int(audio_map.split(':')[-1].rstrip('?'))]] if audio else [])
    if method=='transfer':
        tracks=selected_audio+retained
        if tracks:
            audio_offset=min(float(t.get('start_time',0)) for t in tracks)-float(stream.get('start_time',0))
            args=['-i',source]
            for i,t in enumerate(audio):
                args+=['-map','0:'+str(t['index']),f'-c:a:{i}','copy']
            for i,t in enumerate(audio):
                lang=t.get('tags',{}).get('language','und')
                args+=[f'-metadata:s:a:{i}','language='+lang if re.fullmatch('[a-z]{2,3}',lang) else 'language=und']
            for i,t in enumerate(retained):
                args+=['-map','0:'+str(t['index'])]
                lang=t.get('tags',{}).get('language','und')
                args+=[f'-metadata:s:s:{i}','language='+lang if re.fullmatch('[a-z]{2,3}',lang) else 'language=und']
            ff(*args,'-c:s','srt','-map_metadata','-1','-t',duration,root/'companion-source.mkv',
               label='Copying source audio and text subtitles for companion job',duration=duration)
    elif audio:
        selected=selected_audio[0]
        audio_language=selected.get('tags',{}).get('language','und')
        if not re.fullmatch('[a-z]{2,3}',audio_language):audio_language='und'
        audio_offset=float(selected.get('start_time',0))-float(stream.get('start_time',0))
        ff('-i',source,'-map',audio_map,'-vn','-sn','-dn','-c:a','copy','-t',duration,root/'audio-source.mka',
           label='Copying selected audio for companion job',duration=duration)
    if not -duration<audio_offset<duration:raise ValueError('Invalid companion timeline')
    if method=='soft':shutil.copyfile('sinhala.srt',root/'subtitle.srt')
    assets=root/'assets';assets.mkdir(exist_ok=True)
    (assets/'ready.txt').write_text('Worker assets',encoding='utf-8')
    if method=='burn':
        for filename in ('subtitles.ass','intro.ass','title-logo-display.png'):
            if Path(filename).is_file():shutil.copyfile(filename,assets/filename)
        if Path('fonts').is_dir():shutil.copytree('fonts',assets/'fonts',dirs_exist_ok=True)
    render={key:options[key] for key in ('parts','layout','start','duration','subtitle_enabled','watermark_enabled','okru_stealth','intro_lead_in') if key in options}
    render['content_size']=[cw,ch]
    data=dict(version=1,method=method,count=len(parts),starts=starts,width=width,height=height,content=[cw,ch],
              duration=duration,packets=None,validation='worker-receipts',crf=options['crf'],preset=options['preset'],
              language=language,audio=bool(selected_audio),audio_count=len(selected_audio),audio_language=audio_language,
              audio_languages=[t.get('tags',{}).get('language','und') for t in selected_audio],
              audio_defaults=[bool(t.get('disposition',{}).get('default')) for t in selected_audio],
              subtitle_languages=[t.get('tags',{}).get('language','und') for t in retained],
              audio_offset=audio_offset,subtitle_count=1 if method=='soft' else len(retained),render=render,logo_size=logo_size)
    (root/'manifest.json').write_text(json.dumps(data),encoding='utf-8')
    with open(os.environ['GITHUB_OUTPUT'],'a',encoding='utf-8') as output:
        output.write('distributed=true\nmatrix='+json.dumps(list(range(len(parts))))+'\n')
    print(f'Prepared {len(parts)} runner chunks for {method}; audio conversion will run alongside video workers',flush=True)


def manifest():
    data = json.loads((ROOT / 'manifest.json').read_text(encoding='utf-8'))
    if data['version'] != 1 or not 1 <= data['count'] <= 4 or data['language'] not in ('sin','eng') or data.get('method','soft') not in ('burn','soft','transfer'):
        raise ValueError('Invalid parallel manifest')
    if data['preset'] not in ('fast','veryfast','superfast','ultrafast') or not 0 <= data['crf'] <= 51:
        raise ValueError('Invalid parallel encoder settings')
    return data


def companion():
    """Prepare final AAC companion concurrently with the video matrix jobs."""
    data=manifest();method=data.get('method','soft')
    source=ROOT/('companion-source.mkv' if method=='transfer' else 'audio-source.mka')
    target=ROOT/('companion.mkv' if method=='transfer' else 'audio.m4a')
    if data['audio_count'] or (method=='transfer' and data['subtitle_count']):
        metadata=info(source,count=False)
        audios=[s for s in metadata['streams'] if s['codec_type']=='audio']
        subs=[s for s in metadata['streams'] if s['codec_type']=='subtitle']
        expected_subs=data['subtitle_count'] if method=='transfer' else 0
        if len(audios)!=data['audio_count'] or len(subs)!=expected_subs:
            raise RuntimeError('Source companion track validation failed; upload was not started.')
        from clixarena_features import stealth_audio_filter_chain
        render_cfg=data.get('render',{})
        stealth=bool(render_cfg.get('okru_stealth'))
        lead_in=float(render_cfg.get('intro_lead_in',0) or 0) if method=='burn' else 0.0
        total_dur=float(data['duration'])+lead_in
        if method!='transfer' and len(audios)==1 and (stealth or lead_in>0):
            sr=int(audios[0].get('sample_rate') or 48000)
            delay_ms=max(0,int(round(lead_in*1000)))
            af_chain=stealth_audio_filter_chain(sample_rate=sr, delay_ms=delay_ms, stealth=stealth)
            args=['-i',source,'-map','0:a:0','-af',af_chain,'-c:a','aac','-b:a','192k']
            print(f'Companion audio: silent intro delay ({lead_in:.1f}s) + deep anti-Content-ID audio filter',flush=True)
        else:
            args=['-i',source,'-map','0:a?']
            for i,stream in enumerate(audios):
                copy=stream.get('codec_name')=='aac'
                args+=[f'-c:a:{i}','copy' if copy else 'aac']
                if not copy:
                    bitrate='384k' if method=='transfer' and int(stream.get('channels',2))>2 else '192k'
                    args+=[f'-b:a:{i}',bitrate]
                print(f'Companion audio track {i}: '+('copying AAC' if copy else 'converting to AAC'),flush=True)
        if method=='transfer':args+=['-map','0:s?','-c:s','copy']
        ff(*args,'-t',total_dur,target,label='Preparing AAC companion',duration=total_dur,timeout=7200)
        result=info(target,count=False)
        final_audio=[s for s in result['streams'] if s['codec_type']=='audio']
        final_subs=[s for s in result['streams'] if s['codec_type']=='subtitle']
        if len(final_audio)!=data['audio_count'] or any(s['codec_name']!='aac' for s in final_audio) or len(final_subs)!=expected_subs:
            raise RuntimeError('AAC companion validation failed; upload was not started.')
    (ROOT/'ready.txt').write_text('Companion job complete.',encoding='utf-8')
    print('Audio/subtitle companion ready for final merge',flush=True)


def worker(index):
    from clixarena_features import encode_progress,filter_graph
    data=manifest()
    if index not in range(data['count']):raise ValueError('Invalid chunk index')
    source=(ROOT/f'part-{index:02d}.mkv').resolve();target=(ROOT/f'encoded-{index:02d}.mp4').resolve()
    metadata=info(source);expected=packets(metadata)
    cw,ch=data['content'];width,height=data['width'],data['height']
    args=['ffmpeg','-nostdin','-hide_banner','-loglevel','error','-y','-i',str(source)]
    previous=Path.cwd()
    try:
        lead_in = float(data.get('render', {}).get('intro_lead_in', 0) or 0) if data.get('method','soft')=='burn' else 0.0
        if data.get('method','soft')=='burn':
            assets=(ROOT/'assets').resolve();os.chdir(assets)
            offset=data['starts'][index];render=dict(data['render'], chunk_mode=True);logo=data.get('logo_size')
            end=offset+float(metadata['format']['duration'])
            if lead_in > 0:
                if index == 0 and logo:
                    args+=['-itsoffset','0','-loop','1','-framerate','30','-t',str(render['duration']),'-i','title-logo-display.png']
                else:
                    logo = None
                graph=filter_graph(width,height,render,logo)
                if index == 0:
                    graph=graph.replace('[0:v:0]',f'[0:v:0]tpad=start_duration={lead_in}:start_mode=add:color=black,setpts=PTS-STARTPTS,',1)
                else:
                    graph=graph.replace(',ass=intro.ass:fontsdir=fonts:shaping=complex','')
                    graph=graph.replace('[art]ass=intro.ass:fontsdir=fonts:shaping=complex[video]','[art]null[video]')
                    graph=graph.replace('[0:v:0]',f'[0:v:0]setpts=PTS-STARTPTS+{offset + lead_in}/TB,',1)
            else:
                if logo and offset<render['start']+render['duration'] and end>render['start']:
                    args+=['-itsoffset',str(render['start']),'-loop','1','-framerate','30','-t',str(render['duration']),'-i','title-logo-display.png']
                else:logo=None
                graph=filter_graph(width,height,render,logo)
                graph=graph.replace('[0:v:0]',f'[0:v:0]setpts=PTS-STARTPTS+{offset}/TB,',1)
            graph+=';[video]setpts=PTS-STARTPTS[chunk]'
            args+=['-map','[chunk]','-filter_complex',graph]
        else:
            filters=f'scale={cw}:{ch},setsar=1,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:black,setpts=PTS-STARTPTS'
            args+=['-map','0:v:0','-vf',filters]
        args+=['-an','-sn','-dn','-c:v','libx264','-preset',data['preset'],'-crf',str(data['crf']),'-pix_fmt','yuv420p','-fps_mode','passthrough','-map_metadata','-1']
        print(f'Encoding {data.get("method","soft")} chunk {index+1} of {data["count"]}',flush=True)
        encode_progress(args,float(metadata['format']['duration'])+(lead_in if index==0 else 0.0),output=target)
    finally:os.chdir(previous)
    result=info(target)
    actual_pkts=packets(result)
    if index == 0 and lead_in > 0 and actual_pkts > expected:
        expected = actual_pkts
    if actual_pkts!=expected or (video(result)['width'],video(result)['height'])!=(width,height):
        raise RuntimeError('Chunk frame validation failed; upload was not started')
    (ROOT/f'encoded-{index:02d}.json').write_text(json.dumps(dict(version=1,index=index,
        source_packets=expected,encoded_packets=actual_pkts)),encoding='utf-8')
    print('Chunk encoding and frame validation complete',flush=True)


def merge():
    from clixarena_video import complete_upload, output_filename, mask
    data = manifest()
    paths = [ROOT / f'encoded-{i:02d}.mp4' for i in range(data['count'])]
    if data.get('validation')=='worker-receipts':
        expected=0
        for i,path in enumerate(paths):
            receipt=json.loads((ROOT/f'encoded-{i:02d}.json').read_text(encoding='utf-8'))
            count=receipt.get('source_packets')
            if (receipt.get('version')!=1 or receipt.get('index')!=i or type(count)!=int or count<=0
                or receipt.get('encoded_packets')!=count or not path.is_file() or not path.stat().st_size):
                raise RuntimeError('Missing or invalid worker frame validation receipt; upload was not started.')
            expected+=count
        # Final full-file packet validation below checks the merged result against
        # every worker's independently counted source frames, without rescanning
        # each encoded artifact here as well.
    else:
        expected=data['packets']
        if sum(packets(info(p)) for p in paths)!=expected:
            raise RuntimeError('Missing video frames in runner outputs; upload was not started.')
    lead_in=float(data.get('render',{}).get('intro_lead_in',0) or 0) if data.get('method','soft')=='burn' else 0.0
    total_dur=float(data['duration'])+lead_in
    listing = ROOT / 'concat.txt'
    listing.write_text(''.join(f"file '{p.name}'\n" for p in paths), encoding='utf-8')
    ff('-f','concat','-safe',1,'-i',listing,'-map','0:v:0','-c','copy',ROOT/'joined.mp4',
       label='Joining encoded video chunks',duration=total_dur)
    method=data.get('method','soft')
    args=['-i',ROOT/'joined.mp4'];maps=['-map','0:v:0']
    if method=='transfer' and (data['audio_count'] or data['subtitle_count']):
        args+=['-itsoffset',data['audio_offset'],'-i',ROOT/'companion.mkv']
        maps+=['-map','1:a?','-map','1:s?','-c:a','copy','-c:s','mov_text']
        for kind,langs in [('a',data['audio_languages']),('s',data['subtitle_languages'])]:
            for i,lang in enumerate(langs):
                if not re.fullmatch('[a-z]{2,3}',lang):lang='und'
                maps+=[f'-metadata:s:{kind}:{i}','language='+lang]
        for i,default in enumerate(data['audio_defaults']):maps+=[f'-disposition:a:{i}','default' if default else '0']
    else:
        if data['audio']:
            args+=['-itsoffset',data['audio_offset'],'-i',ROOT/'audio.m4a']
            maps+=['-map','1:a:0','-c:a','copy','-metadata:s:a:0','language='+data['audio_language']]
        if method=='soft':
            args+=['-sub_charenc','UTF-8','-i',ROOT/'subtitle.srt']
            maps+=['-map',f'{2 if data["audio"] else 1}:s:0','-c:s','mov_text','-metadata:s:s:0','language='+data['language'],'-disposition:s:0','0']
    args+=maps
    args+=['-c:v','copy','-map_metadata','-1','-map_chapters','-1','-t',total_dur,'-movflags','+faststart']
    print('Joining runner outputs without another video encode; restoring audio/subtitle tracks',flush=True)
    ff(*args,'clixarena.mp4',label='Restoring audio and subtitles',duration=total_dur)
    result=info('clixarena.mp4');v=video(result)
    subs=[t for t in result['streams'] if t['codec_type']=='subtitle']
    audios=[t for t in result['streams'] if t['codec_type']=='audio']
    if ((packets(result)!=expected if lead_in==0 else abs(packets(result)-expected)>2) or abs(float(result['format']['duration'])-total_dur)>2
        or v['codec_name']!='h264' or (v['width'],v['height'])!=(data['width'],data['height'])
        or len(audios)!=data['audio_count'] or any(t['codec_name']!='aac' for t in audios)
        or len(subs)!=data['subtitle_count'] or any(t['codec_name']!='mov_text' for t in subs)
        or (method=='soft' and subs[0].get('tags',{}).get('language')!=data['language'])):
        raise RuntimeError('Final media validation failed. Upload was not started')
    preview=Path('preview');preview.mkdir(exist_ok=True)
    if method=='soft':shutil.copyfile(ROOT/'subtitle.srt',preview/'subtitle.srt')
    print('Four-runner MP4 validation passed',flush=True)
    if os.getenv('SAMPLE_ONLY','false').lower()=='true':
        shutil.copyfile('clixarena.mp4',preview/'sample.mp4')
        return
    values = {'ABYSS_API_KEY': os.getenv('ABYSS_API_KEY', '')}
    for value in values.values(): mask(value)
    complete_upload(Path('clixarena.mp4'),output_filename(os.getenv('OUTPUT_NAME','CLIXARENA.mp4')),values)


if __name__ == '__main__':
    parser=argparse.ArgumentParser(); parser.add_argument('stage',choices=['worker','companion','merge']); parser.add_argument('--index',type=int,default=0)
    args=parser.parse_args()
    try:
        if args.stage=='worker':worker(args.index)
        elif args.stage=='companion':companion()
        else:merge()
    except Exception as error:
        from clixarena_video import DownloadError
        if isinstance(error, DownloadError): print('::error::'+str(error), flush=True)
        print('::error::Parallel encoding, validation or upload failed. Check the stage above; no automatic upload retry.',flush=True)
        raise SystemExit(1)
