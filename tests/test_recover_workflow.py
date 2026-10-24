import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clixarena_recover as recovery


class RecoveryTests(unittest.TestCase):
    def source(self):
        return {'status':'completed','conclusion':'failure','event':'workflow_dispatch',
                'path':'.github/workflows/clixarena-video.yml','head_repository':{'full_name':'owner/repo'}}
    def logs(self):
        return '\n'.join('prepare\tstep\t2026-10-24T00:00:00Z   '+line for line in [
            'OVERLAY_LAYOUT: {"uploadHost":"okru","mapping":{"type":"movie","tmdb_id":123,"season":0,"episode":0}}',
            'OUTPUT_NAME: Movie.mp4','SAMPLE_ONLY: false','SUBTITLE_PATH: auto',
            'PORTAL_JOB_ID: abc','Four-runner MP4 validation passed'])
    def fake(self, logs=None, run=None, expired=False):
        def gh(*args):
            if args[0]=='run':
                return self.logs() if logs is None else logs
            if '--slurp' in args:
                return json.dumps([{'artifacts':[{'name':n,'expired':expired} for n in [
                    'parallel-manifest','parallel-companion','parallel-encoded-00','parallel-encoded-01']]}])
            return json.dumps(self.source() if run is None else run)
        return gh
    def invoke(self, gh, **extra):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                env={'SOURCE_RUN_ID':'12345','GITHUB_REPOSITORY':'owner/repo','RECOVERY_LAYOUT':'','RECOVERY_OUTPUT_NAME':''}
                env.update(extra)
                with patch.dict(os.environ,env),patch.object(recovery,'gh',side_effect=gh):
                    recovery.prepare()
                return json.loads(recovery.CONTEXT.read_text())
            finally:
                os.chdir(previous)
    def test_restores_original_host_mapping_and_upload_setting(self):
        context=self.invoke(self.fake())
        self.assertEqual(json.loads(context['OVERLAY_LAYOUT'])['uploadHost'],'okru')
        self.assertEqual(json.loads(context['OVERLAY_LAYOUT'])['mapping']['tmdb_id'],123)
        self.assertEqual(context['SAMPLE_ONLY'],'false')
        self.assertEqual(context['OUTPUT_NAME'],'Movie.mp4')
    def test_blocks_already_uploaded_source(self):
        with self.assertRaisesRegex(ValueError,'host upload activity'):
            self.invoke(self.fake(logs=self.logs()+'\nOk.ru upload server validated'))
    def test_blocks_successful_and_foreign_runs(self):
        run=self.source();run['conclusion']='success'
        with self.assertRaisesRegex(ValueError,'completed failed'):
            self.invoke(self.fake(run=run))
        run=self.source();run['head_repository']['full_name']='other/repo'
        with self.assertRaises(ValueError):
            self.invoke(self.fake(run=run))
    def test_missing_settings_do_not_default_to_wrong_host(self):
        with self.assertRaisesRegex(ValueError,'Original layout'):
            self.invoke(self.fake(logs='SAMPLE_ONLY: false'))
    def test_expired_artifacts_stop_before_logs_or_upload(self):
        with self.assertRaisesRegex(ValueError,'missing/expired'):
            self.invoke(self.fake(expired=True))
    def test_conflicting_settings_fail_closed(self):
        with self.assertRaisesRegex(ValueError,'conflicting'):
            recovery.extract_context(self.logs()+'\nSAMPLE_ONLY: true')
    def test_recovery_invokes_merge_only_with_restored_context(self):
        previous=Path.cwd()
        with tempfile.TemporaryDirectory() as tmp:
            try:
                os.chdir(tmp)
                root=Path('parallel-media');root.mkdir()
                (root/'manifest.json').write_text(json.dumps({'version':1,'count':1,'language':'eng','preset':'veryfast','crf':18,'render':{}}))
                (root/'encoded-00.mp4').write_bytes(b'encoded')
                recovery.CONTEXT.write_text(json.dumps({'SAMPLE_ONLY':'false','OVERLAY_LAYOUT':'{"uploadHost":"abyss"}','SOURCE_RUN_ID':'12345'}))
                with patch.object(recovery.subprocess,'call',return_value=0) as call:
                    self.assertEqual(recovery.merge(),0)
                self.assertEqual(call.call_args.args[0][-1],'merge')
                self.assertEqual(call.call_args.kwargs['env']['OVERLAY_LAYOUT'],'{"uploadHost":"abyss"}')
            finally:
                os.chdir(previous)
