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
