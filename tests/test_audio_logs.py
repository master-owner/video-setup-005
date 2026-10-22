import base64,contextlib,io,json,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_video as p

class AudioLogTests(unittest.TestCase):
 def test_multi_audio_is_listed_before_waiting_and_selected_track_is_explicit(self):
  tracks=[{'codec_type':'audio','codec_name':'aac','channels':2,'tags':{'language':'hin'}},{'codec_type':'audio','codec_name':'eac3','channels':6,'tags':{'language':'tam'}}]
  stored={};reads=[0];now=[0]
  def request(job,payload=None,sha=None):
   if payload is not None:stored.update(payload);return {}
   reads[0]+=1
   current=dict(stored)
   if reads[0]>=3:current.update(status='selected',selected=1)
   return {'sha':'revision','content':base64.b64encode(json.dumps(current).encode()).decode()}
  output=io.StringIO()
  with patch.dict(os.environ,{'PORTAL_JOB_ID':'a'*32,'GITHUB_RUN_ID':'123'}),patch.object(p,'audio_job_request',side_effect=request),contextlib.redirect_stdout(output):
   result=p.portal_audio_selection(tracks,'ask',pause=lambda seconds:now.__setitem__(0,now[0]+seconds),clock=lambda:now[0])
  text=output.getvalue();self.assertEqual(result,'0:a:1')
  self.assertLess(text.index('Audio track 1:'),text.index('Multiple audio tracks found'))
  self.assertIn('Waiting for audio track selection',text)
  self.assertIn('Maximum wait: 10 minutes',text)
  self.assertLessEqual(now[0],2)
  self.assertIn('Selected audio: track 1; language=tam, codec=eac3, channels=6',text)
  self.assertEqual(text.count('Audio track 1:'),1)
  self.assertEqual(stored['status'],'encoding')

 def test_default_wait_expires_at_ten_minutes_and_logs_heartbeat(self):
  now=[0];stored={};reads=[]
  tracks=[{'codec_type':'audio','codec_name':'aac','channels':2} for _ in range(2)]
  def request(job,payload=None,sha=None):
   if payload is not None:stored.update(payload);return {}
   reads.append(now[0]);return {'sha':'revision','content':base64.b64encode(json.dumps(stored).encode()).decode()}
  output=io.StringIO()
  with patch.dict(os.environ,{'PORTAL_JOB_ID':'a'*32,'GITHUB_RUN_ID':'123'}),patch.object(p,'audio_job_request',side_effect=request),contextlib.redirect_stdout(output):
   with self.assertRaises(ValueError):p.portal_audio_selection(tracks,'ask',pause=lambda seconds:now.__setitem__(0,now[0]+seconds),clock=lambda:now[0])
  self.assertEqual(now[0],600);self.assertLess(max(reads),600)
  self.assertEqual(stored['status'],'expired');self.assertIn('expires_at',stored)
  self.assertIn('Waiting for audio selection in the control page',output.getvalue())
  self.assertIn('timed out after 10 minutes',output.getvalue());self.assertNotIn('Selected audio confirmed',output.getvalue())

 def test_selection_observed_after_deadline_does_not_resume(self):
  now=[0];stored={}
  tracks=[{'codec_type':'audio','codec_name':'aac','channels':2} for _ in range(2)]
  def request(job,payload=None,sha=None):
   if payload is not None:stored.update(payload);return {}
   result=dict(stored)
   if stored.get('status')=='waiting_audio':now[0]=601;result.update(status='selected',selected=1)
   return {'sha':'revision','content':base64.b64encode(json.dumps(result).encode()).decode()}
  output=io.StringIO()
  with patch.dict(os.environ,{'PORTAL_JOB_ID':'a'*32,'GITHUB_RUN_ID':'123'}),patch.object(p,'audio_job_request',side_effect=request),contextlib.redirect_stdout(output):
   with self.assertRaises(ValueError):p.portal_audio_selection(tracks,'ask',pause=lambda seconds:None,clock=lambda:now[0])
  self.assertEqual(stored['status'],'expired');self.assertNotIn('Selected audio confirmed',output.getvalue())

 def test_fast_polling_reuses_etag_until_selection_changes(self):
  class Response:
   def __init__(self,tag,data):self.headers={'ETag':tag};self.data=data
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,limit):return json.dumps(self.data).encode()
  calls=[]
  def opened(request,timeout):
   calls.append(request)
   if len(calls)==2:raise p.urllib.error.HTTPError(request.full_url,304,'Not modified',{},None)
   return Response('"v1"' if len(calls)==1 else '"v2"',{'content':'waiting' if len(calls)==1 else 'selected'})
  p._AUDIO_STATE_CACHE.clear()
  env={'GITHUB_REPOSITORY':'owner/repo','GITHUB_REF_NAME':'main','MAPPING_GITHUB_TOKEN':'mock-token'}
  with patch.dict(os.environ,env),patch.object(p.urllib.request,'build_opener') as factory:
   factory.return_value.open.side_effect=opened
   self.assertEqual(p.audio_job_request('a'*32)['content'],'waiting')
   self.assertEqual(p.audio_job_request('a'*32)['content'],'waiting')
   self.assertEqual(p.audio_job_request('a'*32)['content'],'selected')
  self.assertIsNone(calls[0].get_header('If-none-match'))
  self.assertEqual(calls[1].get_header('If-none-match'),'"v1"');self.assertEqual(calls[2].get_header('If-none-match'),'"v1"')
  p._AUDIO_STATE_CACHE.clear()

 def test_single_track_logs_selection_without_portal_wait(self):
  output=io.StringIO()
  with patch.object(p,'audio_job_request') as request,contextlib.redirect_stdout(output):
   self.assertEqual(p.portal_audio_selection([{'codec_type':'audio','codec_name':'aac','channels':2,'tags':{'language':'eng'}}],'ask'),'0:a:0')
   request.assert_not_called()
  self.assertIn('Selected audio: track 0; language=eng',output.getvalue())

 def test_audio_metadata_logs_cannot_include_injected_urls_or_commands(self):
  output=io.StringIO()
  with contextlib.redirect_stdout(output):p.audio_selection([{'codec_type':'audio','channels':2,'codec_name':'aac\n::error::private','tags':{'language':'https://signed.example/token'}}],'auto')
  self.assertNotIn('signed.example',output.getvalue());self.assertNotIn('::error::',output.getvalue());self.assertIn('language=und, codec=unknown',output.getvalue())

if __name__=='__main__':unittest.main()
