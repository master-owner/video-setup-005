import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from clixarena_features import prepend_standalone_intro

class IntroJoinTests(unittest.TestCase):
    def ff(self, *args):
        subprocess.run(['ffmpeg','-v','error','-y',*map(str,args)],check=True,capture_output=True)
    def probe(self,p):
        return json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',str(p)]))
    def test_mismatched_intro_is_normalized_and_soft_subtitle_shifted(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); intro=d/'intro.mp4'; main=d/'main.mp4'; sub=d/'sub.srt'
            sub.write_text('1\n00:00:00,500 --> 00:00:01,500\nHello\n')
            self.ff('-f','lavfi','-i','color=red:s=320x180:r=30','-f','lavfi','-i','sine=frequency=220:sample_rate=44100','-t','1','-c:v','libx264','-pix_fmt','yuv444p','-c:a','aac','-ac','1',intro)
            self.ff('-f','lavfi','-i','color=blue:s=640x360:r=24','-f','lavfi','-i','sine=frequency=440:sample_rate=48000','-i',sub,'-t','2','-c:v','libx264','-pix_fmt','yuv420p','-c:a','aac','-ac','2','-c:s','mov_text','-metadata:s:s:0','language=sin',main)
            offset=prepend_standalone_intro(intro,main)
            data=self.probe(main)
            v=next(s for s in data['streams'] if s['codec_type']=='video')
            a=next(s for s in data['streams'] if s['codec_type']=='audio')
            self.assertEqual((v['width'],v['height'],v['pix_fmt']),(640,360,'yuv420p'))
            self.assertEqual((a['sample_rate'],a['channels']),('48000',2))
            self.assertEqual(next(s for s in data['streams'] if s['codec_type']=='subtitle')['tags']['language'], 'sin')
            packets=json.loads(subprocess.check_output(['ffprobe','-v','error','-select_streams','s','-show_packets','-of','json',str(main)]))['packets']
            text_packets=[p for p in packets if int(p['size'])>2]
            self.assertAlmostEqual(float(text_packets[0]['pts_time']),offset+.5,delta=.06)
            self.ff('-xerror','-i',main,'-f','null','-')
    def test_invalid_intro_fails_without_replacing_main(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); main=d/'main.mp4'
            self.ff('-f','lavfi','-i','color=blue:s=160x90:r=24','-t','1','-c:v','libx264',main)
            original=main.read_bytes()
            with self.assertRaises(ValueError):
                prepend_standalone_intro(d/'missing.mp4',main)
            self.assertEqual(main.read_bytes(),original)

    def test_silent_main_does_not_gain_audio_and_content_survives_cut(self):
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); intro=d/'intro.mp4'; main=d/'main.mp4'
            self.ff('-f','lavfi','-i','color=red:s=160x90:r=30','-f','lavfi','-i','sine=frequency=200','-t','1','-c:v','libx264','-c:a','aac',intro)
            self.ff('-f','lavfi','-i','color=blue:s=320x180:r=24','-t','2','-c:v','libx264','-pix_fmt','yuv420p',main)
            offset=prepend_standalone_intro(intro,main)
            self.assertFalse(any(s['codec_type']=='audio' for s in self.probe(main)['streams']))
            for t, channel in [(.4,0),(offset+.4,2)]:
                pixel=subprocess.check_output(['ffmpeg','-v','error','-ss',str(t),'-i',str(main),'-frames:v','1','-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
                self.assertGreater(pixel[channel], 180)
            self.ff('-xerror','-i',main,'-f','null','-')
            # A browser must not need a different H.264 SPS after the intro.
            import re
            raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(main),'-map','0:v:0','-c','copy','-bsf:v','h264_mp4toannexb','-f','h264','-'])
            nals=re.split(b'\x00\x00(?:\x00)?\x01',raw)
            sps={nal for nal in nals if nal and nal[0] & 31 == 7}
            self.assertEqual(len(sps),1)

    def test_nominal_rate_wins_over_chunk_average(self):
        from clixarena_features import intro_frame_rate
        self.assertEqual(intro_frame_rate({'r_frame_rate':'24000/1001','avg_frame_rate':'191808/8009'}),'24000/1001')
        self.assertEqual(intro_frame_rate({'r_frame_rate':'0/0','avg_frame_rate':'25/1'}),'25')
        with self.assertRaises(ValueError):
            intro_frame_rate({'r_frame_rate':'0/0','avg_frame_rate':'0/0'})

    def test_failed_encoder_reports_stage_and_redacts_urls(self):
        from unittest.mock import patch
        from clixarena_features import IntroJoinError
        real_run=subprocess.run
        with tempfile.TemporaryDirectory() as d:
            d=Path(d); main=d/'main.mp4'; intro=d/'intro.mp4'
            self.ff('-f','lavfi','-i','color=blue:s=160x90:r=24','-t','1','-c:v','libx264',main)
            intro.write_bytes(main.read_bytes())
            original=main.read_bytes()
            def fake(args, **kwargs):
                if args[0]=='ffmpeg':
                    return subprocess.CompletedProcess(args,1,'','No space left on device https://example.com/secret')
                return real_run(args,**kwargs)
            with patch('clixarena_features.subprocess.run',side_effect=fake):
                with self.assertRaisesRegex(IntroJoinError,'Normalizing standalone intro failed') as err:
                    prepend_standalone_intro(intro,main)
            self.assertIn('No space left on device',str(err.exception))
            self.assertNotIn('example.com',str(err.exception))
            self.assertEqual(main.read_bytes(),original)

    def test_four_runner_merge_with_intro(self):
        import concurrent.futures
        import os
        from unittest.mock import patch
        import clixarena_distributed as distributed
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            try:
                os.chdir(root)
                self.ff('-f','lavfi','-i','color=blue:s=320x180:r=24','-f','lavfi','-i','sine=frequency=440','-t','8','-c:v','libx264','-g','24','-sc_threshold','0','-c:a','aac','source.video.mp4')
                self.ff('-f','lavfi','-i','color=red:s=160x90:r=30','-t','1','-c:v','libx264','standalone-intro.mp4')
                Path('sinhala.srt').write_text('1\n00:00:01,000 --> 00:00:06,000\nHello\n')
                source=distributed.info('source.video.mp4')
                with patch.dict(os.environ,{'GITHUB_OUTPUT':str(root/'outputs'),'SAMPLE_ONLY':'true'}):
                    distributed.prepare(root,source['streams'],'0:a:0',8,{'preset':'veryfast','crf':18},'eng',method='soft',source='source.video.mp4')
                    self.assertEqual(distributed.manifest()['count'],4)
                    with concurrent.futures.ThreadPoolExecutor(4) as pool:
                        list(pool.map(distributed.worker,range(4)))
                    distributed.companion()
                    distributed.merge()
                self.assertFalse((distributed.ROOT/'joined.mp4').exists())
                result=self.probe('preview/sample.mp4')
                self.assertAlmostEqual(float(result['format']['duration']),9,delta=.2)
                self.ff('-xerror','-i','preview/sample.mp4','-f','null','-')
            finally:
                os.chdir(previous)
