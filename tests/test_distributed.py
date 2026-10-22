import concurrent.futures,json,os,shutil,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_distributed as d

class DistributedTests(unittest.TestCase):
 def test_standard_canvas(self):
  for w,h,tier in [(1920,800,1080),(1282,534,720),(1280,536,720),(1916,1080,1080)]:
   self.assertEqual(d.standard_height({'width':w,'height':h}),tier)

 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
 def test_four_workers_mux_preserves_frames_selected_audio_and_subtitle(self):
  scripts=Path(d.__file__).parent
  previous=Path.cwd()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   try:
    os.chdir(root)
    (root/'sinhala.srt').write_text('1\n00:00:01,000 --> 00:00:06,000\nà·ƒà·’à¶‚à·„à¶½ à¶´à¶»à·“à¶šà·Šà·‚à¶«à¶º\n',encoding='utf-8')
    (root/'old.srt').write_text('1\n00:00:00,000 --> 00:00:07,000\nOLD CAPTION\n',encoding='utf-8')
    d.ff('-f','lavfi','-i','testsrc2=size=640x266:rate=24:duration=8','-f','lavfi','-i','sine=frequency=440:duration=8','-f','lavfi','-i','sine=frequency=880:duration=8,asetpts=PTS+0.5/TB','-i','old.srt','-map','0:v','-map','1:a','-map','2:a','-map','3:s','-c:v','libx264','-preset','veryfast','-g',24,'-sc_threshold',0,'-c:a','aac','-c:s','srt','-metadata:s:a:0','language=hin','-metadata:s:a:1','language=tam','-t',8,'-f','matroska','source.video')
    source=d.info('source.video')
    with patch.dict(os.environ,{'GITHUB_OUTPUT':str(root/'outputs'),'SAMPLE_ONLY':'true'}):
     d.prepare(root,source['streams'],'0:a:1',8,{'preset':'veryfast','crf':18},'sin')
     manifest=d.manifest();self.assertEqual(manifest['count'],4)
     with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
      list(pool.map(d.worker,range(4)))
     d.companion();d.merge()
    result=d.info('clixarena.mp4');v=d.video(result)
    self.assertEqual(d.packets(result),d.packets(source));self.assertEqual(d.packets(result),192)
    self.assertEqual((v['width'],v['height']),(640,360))
    audios=[s for s in result['streams'] if s['codec_type']=='audio'];self.assertEqual(len(audios),1);self.assertEqual(audios[0]['tags']['language'],'tam');self.assertAlmostEqual(float(audios[0]['start_time']),d.manifest()['audio_offset'],delta=0.06)
    self.assertLess(abs(float(result['format']['duration'])-8),0.15)
    text=d.run(['ffmpeg','-v','error','-i','clixarena.mp4','-map','0:s:0','-f','srt','-']).decode('utf-8')
    self.assertIn('à·ƒà·’à¶‚à·„à¶½ à¶´à¶»à·“à¶šà·Šà·‚à¶«à¶º',text);self.assertNotIn('OLD CAPTION',text)
    with patch('clixarena_video.complete_upload') as upload:
     (d.ROOT/'encoded-03.mp4').unlink()
     with self.assertRaises((RuntimeError,FileNotFoundError)):d.merge()
     upload.assert_not_called()
    with self.assertRaises(ValueError): d.worker(4)
   finally:os.chdir(previous)

 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
 def test_short_single_keyframe_source_without_audio(self):
  previous=Path.cwd()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   try:
    os.chdir(root)
    (root/'sinhala.srt').write_text('1\n00:00:00,000 --> 00:00:01,200\nEnglish caption\n',encoding='utf-8')
    d.ff('-f','lavfi','-i','color=black:size=320x180:rate=24:duration=1.5','-c:v','libx264','-preset','veryfast','-g',240,'-f','matroska','source.video')
    source=d.info('source.video')
    with patch.dict(os.environ,{'GITHUB_OUTPUT':str(root/'outputs'),'SAMPLE_ONLY':'true'}):
     d.prepare(root,source['streams'],'0:a:0?',1.5,{'preset':'veryfast','crf':20},'eng')
     self.assertEqual(d.manifest()['count'],1)
     d.worker(0);d.companion();d.merge()
    result=d.info('clixarena.mp4')
    self.assertEqual(d.packets(result),36)
    self.assertEqual(sum(s['codec_type']=='audio' for s in result['streams']),0)
    self.assertEqual(next(s for s in result['streams'] if s['codec_type']=='subtitle')['tags']['language'],'eng')
   finally:os.chdir(previous)

 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
 def test_burn_keeps_global_subtitle_and_animated_logo_times_across_chunks(self):
  previous=Path.cwd();script=Path(d.__file__).resolve()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   try:
    os.chdir(root)
    d.ff('-f','lavfi','-i','color=black:size=640x266:rate=24:duration=8','-c:v','libx264','-g',24,'-sc_threshold',0,'-f','matroska','source.video')
    header='[Script Info]\nScriptType: v4.00+\nPlayResX: 640\nPlayResY: 360\n[V4+ Styles]\nFormat: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding\nStyle: Default,Arial,30,&H00FFFFFF,&H00FFFFFF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,1,0,2,10,10,20,1\n[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n'.replace('\\n','\n')
    Path('subtitles.ass').write_text(header+'Dialogue: 0,0:00:01.00,0:00:06.50,Default,,0,0,0,,BOUNDARY CAPTION\n',encoding='utf-8')
    d.ff('-f','lavfi','-i','color=red:size=100x40','-frames:v',1,'title-logo-display.png')
    options={'height':360,'preset':'veryfast','crf':18,'parts':{'name':False,'message':False,'logo':True},'layout':{'positions':{'logo':{'x':50,'y':40}}},'start':2.5,'duration':3,'subtitle_enabled':True,'watermark_enabled':False}
    with patch.dict(os.environ,{'GITHUB_OUTPUT':str(root/'outputs'),'SAMPLE_ONLY':'true'}):
     d.prepare(root,d.info('source.video')['streams'],'0:a:0?',8,options,'eng','burn',logo_size=[100,40])
     self.assertEqual(d.manifest()['count'],4)
     self.assertEqual([round(x,2) for x in d.manifest()['starts']],[0,2,4,6])
     def execute(i):
      result=subprocess.run([sys.executable,str(script),'worker','--index',str(i)],cwd=root,capture_output=True)
      self.assertEqual(result.returncode,0,result.stdout.decode(errors='replace')+result.stderr.decode(errors='replace'))
     with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(execute,range(4)))
     d.companion();d.merge()
    output=d.info('clixarena.mp4');self.assertEqual(d.packets(output),192)
    self.assertFalse(any(t['codec_type']=='subtitle' for t in output['streams']))
    def pixels(t):
     raw=d.run(['ffmpeg','-v','error','-ss',str(t),'-i','clixarena.mp4','-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
     white=sum(r>150 and g>150 and b>150 for r,g,b in zip(raw[0::3],raw[1::3],raw[2::3]))
     red=sum(r>60 and g<30 and b<30 for r,g,b in zip(raw[0::3],raw[1::3],raw[2::3]))
     return white,red
    self.assertLess(pixels(0.5)[0],10)
    for t in [1.5,2.25,4.25,6.25]:self.assertGreater(pixels(t)[0],100)
    self.assertLess(pixels(7.5)[0],10)
    self.assertLess(pixels(2.25)[1],20);self.assertGreater(pixels(4.25)[1],1000);self.assertLess(pixels(5.75)[1],20)
   finally:os.chdir(previous)

 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
 def test_transfer_conversion_retains_all_audio_and_source_text_subtitles(self):
  previous=Path.cwd()
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)
   try:
    os.chdir(root)
    Path('old.srt').write_text('1\n00:00:00,500 --> 00:00:03,500\nSource caption\n',encoding='utf-8')
    d.ff('-f','lavfi','-i','color=black:size=320x180:rate=12:duration=4','-f','lavfi','-i','sine=frequency=440:duration=4','-f','lavfi','-i','sine=frequency=880:duration=4','-i','old.srt','-map','0:v','-map','1:a','-map','2:a','-map','3:s','-c:v','mpeg4','-g',12,'-c:a:0','aac','-c:a:1','ac3','-c:s','srt','-metadata:s:a:0','language=eng','-metadata:s:a:1','language=tam','-metadata:s:s:0','language=eng','-t',4,'-f','matroska','transfer.video')
    with patch.dict(os.environ,{'GITHUB_OUTPUT':str(root/'outputs'),'SAMPLE_ONLY':'true'}):
     d.prepare(root,d.info('transfer.video')['streams'],None,4,{'height':None,'preset':'veryfast','crf':20},method='transfer',source='transfer.video')
     self.assertEqual(d.manifest()['count'],4)
     with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:list(pool.map(d.worker,range(4)))
     d.companion();d.merge()
    output=d.info('clixarena.mp4');self.assertEqual(d.packets(output),48)
    self.assertEqual((d.video(output)['width'],d.video(output)['height']),(320,180))
    audios=[t for t in output['streams'] if t['codec_type']=='audio'];self.assertEqual(len(audios),2);self.assertTrue(all(t['codec_name']=='aac' for t in audios))
    self.assertEqual([t['tags']['language'] for t in audios],['eng','tam'])
    self.assertEqual(sum(t['codec_type']=='subtitle' for t in output['streams']),1)
   finally:os.chdir(previous)

 def test_workflow_uses_four_real_jobs_and_only_successful_finalization(self):
  workflow=(Path(__file__).resolve().parents[1]/'.github/workflows/clixarena-video.yml').read_text(encoding='utf-8')
  self.assertIn('max-parallel: 4',workflow)
  self.assertIn('needs: [encode, chunks, companion]',workflow)
  self.assertIn("success() && needs.encode.outputs.distributed == 'true'",workflow)
  worker=workflow.split('  chunks:')[1].split('  companion:')[0]
  self.assertNotIn('secrets.',worker)
  self.assertIn("DISTRIBUTED_PREPARE: 'true'",workflow)

if __name__=='__main__':unittest.main()
