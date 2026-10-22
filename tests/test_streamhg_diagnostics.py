import contextlib,io,json,os,sys,unittest,urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_hosts as h
import clixarena_video as p

class Diagnostics(unittest.TestCase):
 def test_api_http_status_never_leaks_url_or_response(self):
  def fail(*args,**kwargs):raise urllib.error.HTTPError('https://private/?key=MYSECRET',403,'MYSECRET',{},None)
  with patch.object(h.urllib.request,'build_opener',return_value=SimpleNamespace(open=fail)):
   with self.assertRaises(h.HostError) as caught:h.streamhg_api('upload/server','MYSECRET')
  self.assertIn('HTTP 403',str(caught.exception));self.assertNotIn('MYSECRET',str(caught.exception));self.assertNotIn('https:',str(caught.exception))
 def test_upload_http_diagnostic_is_not_retried(self):
  calls=[]
  def run(*args,**kw):calls.append(1);return SimpleNamespace(returncode=22,stdout='\nCLIXARENA_HTTP:403',stderr='https://secret MYSECRET')
  with patch.object(h,'streamhg_api',return_value={'result':'https://upload.example/upload/01'}),contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaises(h.HostError) as caught:h.upload_streamhg(Path('x.mp4'),'x.mp4','MYSECRET',run=run,validate=lambda _:None)
  self.assertEqual(calls,[1]);self.assertIn('curl 22, HTTP 403',str(caught.exception));self.assertNotIn('MYSECRET',str(caught.exception))
 def test_non_json_and_missing_id_are_distinct(self):
  for body,expected in [('HTML MYSECRET','expected JSON'),(json.dumps({'status':200,'files':[{'status':'OK'}]}),'no valid filecode')]:
   with patch.object(h,'streamhg_api',return_value={'result':'https://upload.example/upload/01'}),contextlib.redirect_stdout(io.StringIO()):
    with self.assertRaises(h.HostError) as caught:h.upload_streamhg(Path('x.mp4'),'x.mp4','MYSECRET',run=lambda *a,**k:SimpleNamespace(returncode=0,stdout=body+'\nCLIXARENA_HTTP:200'),validate=lambda _:None)
   self.assertIn(expected,str(caught.exception));self.assertNotIn('MYSECRET',str(caught.exception))
 def test_pipeline_preserves_fixed_stage_error(self):
  message='StreamHG file upload: TLS certificate verification failed (curl 60, HTTP unknown).'
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"streamhg"}'}),patch.object(p,'upload_streamhg',side_effect=h.HostError(message)),patch.object(p,'save_mapping',side_effect=AssertionError('Should not map failed upload')),contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaises(p.DownloadError) as caught:p.complete_upload(None,'x.mp4',{'STREAMHG_API_KEY':'secret'})
  self.assertEqual(str(caught.exception),message)
