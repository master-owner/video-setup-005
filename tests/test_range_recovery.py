import io,sys,tempfile,unittest,urllib.error,http.client
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_parallel as d
class Response(io.BytesIO):
 def __init__(self,body,first,last,total,fail=False):
  super().__init__(body);self.status=206;self.fail=fail;self.headers={'ETag':'"stable"','Content-Range':f'bytes {first}-{last}/{total}','Content-Length':str(last-first+1)}
 def read(self,n=-1):
  if self.fail:self.fail=False;raise http.client.IncompleteRead(b'partial',10)
  return super().read(n)
class RecoveryTests(unittest.TestCase):
 def test_failed_chunk_only_is_retried_and_successful_chunks_are_preserved(self):
  data=bytes(range(256))*4096;calls={};logs=[]
  def open(req,timeout):
   a,b=map(int,req.get_header('Range')[6:].split('-'));key=(a,b);calls[key]=calls.get(key,0)+1
   return Response(data[a:b+1],a,b,len(data),fail=a==262144 and calls[key]==1)
  with tempfile.TemporaryDirectory() as tmp,patch.object(d,'MIN_SIZE',1),patch.object(d,'CHUNK_SIZE',262144),patch.object(d.shutil,'disk_usage',return_value=SimpleNamespace(free=10**12)),patch.object(d.time,'sleep'):
   target=Path(tmp)/'video'
   self.assertTrue(d._parallel_download_once('https://example.com/video',target,len(data),lambda:SimpleNamespace(open=open),lambda _:None,log=lambda m,**kw:logs.append(m),connections=4))
   self.assertEqual(target.read_bytes(),data)
  self.assertEqual(calls[(262144,524287)],2)
  self.assertEqual(sum(calls.values()),6)
  self.assertIn('Retrying interrupted video range; completed ranges retained',logs)
 def test_transient_probe_failure_does_not_skip_remaining_connection_counts(self):
  calls=[]
  def open(req,timeout):raise urllib.error.URLError('secret-url-must-not-appear')
  with patch.object(d.time,'sleep'):
   result=d._parallel_download_once('https://example.com/private',Path('unused'),100,lambda:SimpleNamespace(open=open),lambda _:None,log=lambda m,**kw:calls.append(m),connections=3)
  self.assertIsNone(result)
  self.assertNotIn('secret',' '.join(calls))
if __name__=='__main__':unittest.main()
