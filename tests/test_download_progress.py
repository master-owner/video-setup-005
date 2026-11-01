import contextlib,io,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from clixarena_download_progress import DownloadProgress
import clixarena_video as pipeline


class DownloadProgressTests(unittest.TestCase):
 def test_parallel_aggregate_retry_reset_and_no_early_100(self):
  output=io.StringIO()
  with contextlib.redirect_stdout(output):
   p=DownloadProgress(200,4);p.update(100,0);p.update(100,100);p.emit()
   p.update(0,100);self.assertEqual(sum(p.parts.values()),100)
   p.update(100,100);p.finish()
  self.assertIn('99.9%',output.getvalue());self.assertIn('connections 4',output.getvalue());self.assertEqual(output.getvalue().count('100% complete'),1)
 def test_unknown_size_and_stalled_heartbeat(self):
  output=io.StringIO()
  with contextlib.redirect_stdout(output):
   p=DownloadProgress();p.update(100)
   with patch.object(p.stop,'wait',side_effect=[False,True]):p._heartbeat()
  self.assertIn('total size unknown',output.getvalue());self.assertIn('MiB/s',output.getvalue());self.assertNotIn('100%',output.getvalue())
 def test_incomplete_response_never_logs_complete_and_stops_monitor(self):
  class Response:
   headers={'Content-Length':'200'}
   def __init__(self):self.n=0
   def read(self,*args):self.n+=1;return b'video' if self.n==1 else b''
  output=io.StringIO()
  with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(output):
   with self.assertRaises(pipeline.RetryableDownloadError):pipeline.consume_video_response(Response(),Path(tmp)/'video',1000)
  self.assertNotIn('100% complete',output.getvalue())
 def test_successful_single_stream_emits_completion_without_remote_metadata(self):
  class Response:
   headers={'Content-Length':'5','Content-Disposition':'SECRETURL','ETag':'PRIVATE'}
   def __init__(self):self.n=0
   def read(self,*args):self.n+=1;return b'video' if self.n==1 else b''
  output=io.StringIO()
  with tempfile.TemporaryDirectory() as tmp,contextlib.redirect_stdout(output):
   pipeline.consume_video_response(Response(),Path(tmp)/'video',1000)
  self.assertIn('Download 100% complete',output.getvalue());self.assertNotIn('SECRET',output.getvalue());self.assertNotIn('PRIVATE',output.getvalue())

if __name__=='__main__':unittest.main()
