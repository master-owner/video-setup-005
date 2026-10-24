import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clixarena_features as f

class IntroVoiceTests(unittest.TestCase):
    def test_english_message_uses_sinhala_default_and_brand_is_transliterated(self):
        self.assertIn('සිංහල',f.sinhala_intro_text('Welcome to ClixArena'))
        self.assertNotIn('Welcome',f.sinhala_intro_text('Welcome to ClixArena'))
        self.assertIn('ක්ලික්ස්ඇරීනා ඩොට් ස්ට්‍රීම්', f.sinhala_intro_text('සිංහල online clixarena.stream'))

    def test_custom_english_file_is_not_automatically_used(self):
        previous=Path.cwd()
        real_run=subprocess.run
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                Path('intro-voice.mp3').write_bytes(b'old English custom recording')
                calls=[]
                def run(args,**kwargs):
                    if args[0]=='edge-tts':
                        calls.append(args)
                        # A local audio fixture stands in for network TTS bytes.
                        real_run(['ffmpeg','-v','error','-y','-f','lavfi','-i','sine=frequency=440:duration=1','-c:a','libmp3lame',args[-1]],check=True,capture_output=True)
                        return subprocess.CompletedProcess(args,0,'','')
                    return real_run(args,**kwargs)
                with patch.dict(os.environ,{'INTRO_USE_CUSTOM_VOICE':'false','INTRO_VOICE_TEXT':''}),patch('shutil.which',side_effect=lambda tool: '/fake/edge-tts' if tool=='edge-tts' else None),patch.object(f.subprocess,'run',side_effect=run):
                    result=f.synthesize_intro_voiceover('Welcome English',2)
                self.assertTrue(result.is_file())
                self.assertEqual(calls[0][calls[0].index('--voice')+1],'si-LK-SameeraNeural')
                self.assertIn('සිංහල',calls[0][calls[0].index('--text')+1])
            finally:
                os.chdir(previous)

    def test_missing_sinhala_engines_fails_instead_of_using_english_file(self):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                Path('intro-voice.mp3').write_bytes(b'English')
                with patch.dict(os.environ,{'INTRO_USE_CUSTOM_VOICE':'false'}),patch('shutil.which',return_value=None):
                    with self.assertRaisesRegex(f.IntroJoinError,'Sinhala intro speech'):
                        f.synthesize_intro_voiceover('English',2)
            finally:
                os.chdir(previous)

    def test_retained_intro_voice_is_replaced_without_changing_video(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);intro=root/'intro.mp4';voice=root/'voice.m4a'
            def ff(*args):
                return subprocess.check_output(['ffmpeg','-v','error','-y',*map(str,args)])
            ff('-f','lavfi','-i','color=red:s=160x90:r=24','-f','lavfi','-i','sine=frequency=220:duration=1','-t','1','-c:v','libx264','-c:a','aac',intro)
            ff('-f','lavfi','-i','sine=frequency=880:duration=1','-c:a','aac',voice)
            video_before=ff('-i',intro,'-map','0:v:0','-c','copy','-f','h264','-')
            audio_before=ff('-i',intro,'-map','0:a:0','-f','s16le','-')
            with patch.object(f,'synthesize_intro_voiceover',return_value=voice) as synth:
                f.refresh_intro_voice(intro,'සිංහල')
            self.assertEqual(video_before,ff('-i',intro,'-map','0:v:0','-c','copy','-f','h264','-'))
            self.assertNotEqual(audio_before,ff('-i',intro,'-map','0:a:0','-f','s16le','-'))
            self.assertEqual(synth.call_args.args[0],'සිංහල')
