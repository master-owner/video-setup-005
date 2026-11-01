import contextlib,io,os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_video as p
class Response:
 headers={'Content-Type':'video/mp4'}
 def __enter__(self):return self
 def __exit__(self,*args):self.closed=True
 def geturl(self):return 'https://drive.usercontent.google.com/download?id=test&confirm=private'
 def read(self,*args):raise AssertionError('Resolution must not read video bytes')
class DriveParallel(unittest.TestCase):
 def test_drive_resolves_then_parallel_uses_requested_count_and_independent_cookie_openers(self):
  response=Response();calls=[]
  def parallel(url,target,limit,factory,validate,**kw):
   self.assertIn('drive.usercontent.google.com',url);self.assertEqual(kw['connections'],6);self.assertIsNot(factory(),factory());calls.append(url);return True
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"downloadConnections":6}','PARALLEL_DOWNLOAD':'true'}),patch.object(p,'validate_url'),patch.object(p,'mask'),patch.object(p,'open_video_response',return_value=response),patch.object(p,'parallel_download',parallel),patch.object(p,'_download_once',side_effect=AssertionError('Unexpected single download')),contextlib.redirect_stdout(io.StringIO()):
   p.download('https://drive.google.com/file/d/testfile/view',Path('unused'),10**10)
  self.assertTrue(response.closed);self.assertEqual(len(calls),1)
 def test_range_unavailable_falls_back_to_original_drive_resolver(self):
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"downloadConnections":4}','PARALLEL_DOWNLOAD':'true'}),patch.object(p,'validate_url'),patch.object(p,'mask'),patch.object(p,'open_video_response',return_value=Response()),patch.object(p,'parallel_download',return_value=False),patch.object(p,'_download_once') as single,contextlib.redirect_stdout(io.StringIO()):
   p.download('https://drive.google.com/file/d/testfile/view',Path('unused'),10**10)
  self.assertEqual(single.call_args.args[-1],'testfile')
 def test_one_connection_skips_drive_parallel_resolution(self):
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"downloadConnections":1}'}),patch.object(p,'validate_url'),patch.object(p,'mask'),patch.object(p,'open_video_response',side_effect=AssertionError('Unneeded resolver')),patch.object(p,'parallel_download',return_value=False),patch.object(p,'_download_once') as single,contextlib.redirect_stdout(io.StringIO()):
   p.download('https://drive.google.com/file/d/testfile/view',Path('unused'),10**10)
  single.assert_called_once()
 def test_direct_and_dropbox_use_parallel(self):
  for url in ('https://example.com/media.mkv','https://www.dropbox.com/s/testfile/movie.mp4?dl=0'):
   with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"downloadConnections":6}'}),patch.object(p,'validate_url'),patch.object(p,'mask'),patch.object(p,'parallel_download',return_value=True) as parallel,patch.object(p,'_download_once',side_effect=AssertionError('Unexpected single download')),contextlib.redirect_stdout(io.StringIO()):p.download(url,Path('unused'),10**10)
   self.assertEqual(parallel.call_args.kwargs['connections'],6)
if __name__=='__main__':unittest.main()
