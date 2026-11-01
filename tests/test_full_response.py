import io,sys,tempfile,unittest,urllib.error,contextlib
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_parallel as d
import clixarena_video as p
class Response(io.BytesIO):
 def __init__(self,body,declared=None,content='video/mkv'):
  super().__init__(body);self.status=200;self.headers={'Content-Type':content,'Content-Length':str(len(body) if declared is None else declared)}
class FullResponseTests(unittest.TestCase):
 def test_full_response_reused_without_another_network_request(self):
  data=b'video-fixture'*100;calls=[];response=Response(data)
  def open(req,timeout):
   calls.append(req)
   if len(calls)>1:raise urllib.error.HTTPError(req.full_url,401,'mock',{},None)
   return response
  with tempfile.TemporaryDirectory() as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)),contextlib.redirect_stdout(io.StringIO()):
   target=Path(tmp)/'video'
   self.assertTrue(d.parallel_download('https://example.com/signed',target,100000,lambda:SimpleNamespace(open=open),lambda _:None,connections=4,consume_full=lambda r:p.consume_video_response(r,target,100000)))
   self.assertEqual(target.read_bytes(),data)
  self.assertEqual(len(calls),1);self.assertTrue(response.closed)
 def test_range_rejection_uses_one_normal_get(self):
  for code in (400,401):
   calls=[];data=b'video-data'
   def open(req,timeout):
    calls.append(req)
    if req.get_header('Range'):raise urllib.error.HTTPError(req.full_url,code,'mock',{},None)
    return Response(data)
   with tempfile.TemporaryDirectory() as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)),contextlib.redirect_stdout(io.StringIO()):
    target=Path(tmp)/'video'
    self.assertTrue(d.parallel_download('https://example.com/signed',target,100000,lambda:SimpleNamespace(open=open),lambda _:None,consume_full=lambda r:p.consume_video_response(r,target,100000)))
    self.assertEqual(target.read_bytes(),data)
   self.assertEqual(len(calls),2);self.assertIsNone(calls[-1].get_header('Range'))
 def test_truncated_full_response_is_not_silently_accepted_or_reopened(self):
  calls=[]
  def open(req,timeout):calls.append(req);return Response(b'short',declared=100)
  with tempfile.TemporaryDirectory() as tmp,patch.object(p.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)),contextlib.redirect_stdout(io.StringIO()):
   target=Path(tmp)/'video'
   with self.assertRaises(p.RetryableDownloadError):d.parallel_download('https://example.com/signed',target,100000,lambda:SimpleNamespace(open=open),lambda _:None,consume_full=lambda r:p.consume_video_response(r,target,100000))
   self.assertFalse(target.exists())
  self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
