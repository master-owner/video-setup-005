import concurrent.futures
import contextlib
import io
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_distributed as d


class PreparationTests(unittest.TestCase):
    def test_stage_timeout_kills_process_and_redacts_errors(self):
        log=io.StringIO()
        with contextlib.redirect_stdout(log):
            with self.assertRaisesRegex(RuntimeError,'timed out'):
                d.run([sys.executable,'-c','import time; time.sleep(5)'],label='Test preparation',timeout=.05)
            with self.assertRaisesRegex(RuntimeError,'failed'):
                d.run([sys.executable,'-c','import sys;sys.stderr.write("PRIVATE_URL_AND_TOKEN");sys.exit(1)'],label='Test preparation')
        self.assertIn('Test preparation',log.getvalue())
        self.assertNotIn('PRIVATE_URL_AND_TOKEN',log.getvalue())

    def test_missing_worker_receipt_prevents_upload(self):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            try:
                os.chdir(temp);d.ROOT.mkdir()
                (d.ROOT/'encoded-00.mp4').write_bytes(b'not validated')
                with patch.object(d,'manifest',return_value={'count':1,'validation':'worker-receipts'}),patch('clixarena_video.complete_upload') as upload:
                    with self.assertRaises(FileNotFoundError):d.merge()
                    upload.assert_not_called()
            finally:os.chdir(previous)

    def test_workflow_audio_and_video_have_same_dependency(self):
        text=(Path(__file__).resolve().parents[1]/'.github/workflows/clixarena-video.yml').read_text(encoding='utf-8')
        audio=text.split('  companion:\n')[1].split('  finalize:\n')[0]
        chunks=text.split('  chunks:\n')[1].split('  companion:\n')[0]
        self.assertIn('needs: encode',audio)
        self.assertIn('needs: encode',chunks)
        self.assertNotIn('secrets.',audio)
        self.assertIn('needs: [encode, chunks, companion]',text)
        self.assertIn('parallel-media/encoded-*.json',text)

    @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
    def test_ac3_conversion_is_deferred_and_six_channels_survive(self):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            try:
                os.chdir(root)
                Path('sinhala.srt').write_text('1\n00:00:01,000 --> 00:00:02,000\nCaption\n',encoding='utf-8')
                d.ff('-f','lavfi','-i','color=black:size=320x180:rate=12:duration=4',
                    '-f','lavfi','-i','anullsrc=channel_layout=5.1:sample_rate=48000',
                    '-map','0:v','-map','1:a','-c:v','libx264','-g',12,'-sc_threshold',0,
                    '-c:a','ac3','-b:a','384k','-metadata:s:a:0','language=eng','-t',4,'-f','matroska','source.video')
                source=d.info('source.video',count=False)
                with patch.dict(os.environ,{'GITHUB_OUTPUT':str(root/'outputs'),'SAMPLE_ONLY':'true'}):
                    with patch.object(d,'info',wraps=d.info) as probes,patch.object(d,'ff',wraps=d.ff) as commands:
                        d.prepare(root,source['streams'],'0:a:0',4,{'preset':'veryfast','crf':20},language='eng')
                        self.assertTrue(all(call.kwargs.get('count') is False for call in probes.call_args_list))
                        self.assertTrue(all('aac' not in call.args for call in commands.call_args_list))
                    self.assertFalse((d.ROOT/'audio.m4a').exists())
                    raw=d.info(d.ROOT/'audio-source.mka',count=False)
                    self.assertEqual(raw['streams'][0]['codec_name'],'ac3')
                    with concurrent.futures.ThreadPoolExecutor(max_workers=5) as pool:
                        jobs=[pool.submit(d.worker,i) for i in range(d.manifest()['count'])]
                        jobs.append(pool.submit(d.companion))
                        for job in jobs:job.result()
                    d.merge()
                result=d.info('clixarena.mp4')
                audio=next(s for s in result['streams'] if s['codec_type']=='audio')
                self.assertEqual(audio['codec_name'],'aac');self.assertEqual(audio['channels'],6)
                self.assertEqual(d.packets(result),48)
            finally:os.chdir(previous)


if __name__=='__main__':unittest.main()
