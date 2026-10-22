import contextlib,io,json,os,sys,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_video as pipeline
from clixarena_hosts import selected_hosts
class DualUploadTests(unittest.TestCase):
 def test_metadata_wait_overlaps_next_upload_but_uploads_stay_ordered(self):
  checking=threading.Event();second=threading.Event();events=[]
  def verify(*args):
   checking.set()
   if not second.wait(3):raise AssertionError('Second upload blocked behind metadata verification')
   events.append('verified');return True,True
  def upload_first(*args,**kwargs):events.append('first-accepted');return 'private-first'
  def upload_second(*args,**kwargs):
   self.assertTrue(checking.wait(3));events.append('second-accepted');second.set();return 'private-second'
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'out.mp4';path.write_bytes(b'video')
   with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"streamhg","secondUploadHost":"abyss"}','GITHUB_OUTPUT':''}),patch.object(pipeline,'upload_streamhg',upload_first),patch.object(pipeline,'upload_abyss',upload_second),patch.object(pipeline,'verify_streamhg',verify),patch.object(pipeline,'verify_abyss',return_value=(True,True)),patch.object(pipeline,'save_mapping'),contextlib.redirect_stdout(io.StringIO()):
    pipeline.complete_upload(path,'Title.mp4',{'STREAMHG_API_KEY':'secret','ABYSS_API_KEY':'secret'})
  self.assertEqual(events[:2],['first-accepted','second-accepted']);self.assertIn('verified',events)
  self.assertIsNone(pipeline._UPLOAD_VERIFIER.get())
 def test_background_failure_is_joined_and_sanitized_without_retry(self):
  calls=[];output=io.StringIO()
  def one(p,n,v,provider):
   calls.append(provider)
   if provider=='abyss':pipeline.verify_accepted_upload(provider,'PRIVATEID','SECRET',1,n)
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"abyss","secondUploadHost":"streamhg"}'}),patch.object(pipeline,'complete_upload_one',one),patch.object(pipeline,'finish_provider_upload',side_effect=ValueError('SECRET PRIVATEID')),contextlib.redirect_stdout(output):
   with self.assertRaises(pipeline.DownloadError):pipeline.complete_upload(Path('out.mp4'),'Title.mp4',{})
  self.assertEqual(calls,['abyss','streamhg']);self.assertNotIn('SECRET',output.getvalue());self.assertNotIn('PRIVATEID',output.getvalue());self.assertIsNone(pipeline._UPLOAD_VERIFIER.get())
 def test_order_validation(self):
  for first,second in [('streamtape','abyss'),('abyss','streamhg'),('streamhg','streamtape')]:
   self.assertEqual(selected_hosts({'uploadHost':first,'secondUploadHost':second}),[first,second])
  self.assertEqual(selected_hosts({}),['streamtape'])
  for second in ('streamtape','unknown',[],None):
   with self.assertRaises(ValueError):selected_hosts({'secondUploadHost':second})
 def test_same_file_order_and_no_second_encode(self):
  path=Path('already-encoded.mp4');calls=[]
  def one(p,name,values,provider):calls.append((p,name,provider))
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':json.dumps({'uploadHost':'abyss','secondUploadHost':'streamhg'})}),patch.object(pipeline,'complete_upload_one',one),contextlib.redirect_stdout(io.StringIO()):
   pipeline.complete_upload(path,'Title.mp4',{})
  self.assertEqual(calls,[(path,'Title.mp4','abyss'),(path,'Title.mp4','streamhg')])
 def test_either_failure_continues_once_and_marks_job_failed_without_raw_error(self):
  for failed in ('streamtape','abyss'):
   calls=[];out=io.StringIO()
   def one(p,n,v,provider):
    calls.append(provider)
    if provider==failed:raise ValueError('SECRET-RAW-ERROR')
   with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"streamtape","secondUploadHost":"abyss"}'}),patch.object(pipeline,'complete_upload_one',one),contextlib.redirect_stdout(out):
    with self.assertRaises(pipeline.DownloadError) as error:pipeline.complete_upload(Path('out.mp4'),'Title.mp4',{})
   self.assertEqual(calls,['streamtape','abyss']);self.assertIn(failed,str(error.exception));self.assertNotIn('SECRET',out.getvalue()+str(error.exception))
 def test_encrypted_receipt_preserves_both_ids_even_when_repository_save_fails(self):
  identity={'type':'movie','tmdb_id':1,'season':0,'episode':0}
  with tempfile.TemporaryDirectory() as directory,patch.dict(os.environ,{'OVERLAY_LAYOUT':json.dumps({'mapping':identity}),'GITHUB_WORKSPACE':directory,'GITHUB_REPOSITORY':'','MAPPING_PASSWORD':'test-password'}),patch.object(pipeline,'external_subtitle_mapping',return_value={}):
   for provider,id in [('streamtape','first-id'),('abyss','second-id')]:
    with self.assertRaises(ValueError):pipeline.save_mapping(id,provider)
   raw=(Path(directory)/'mapping-result.json').read_text();self.assertNotIn('first-id',raw);self.assertNotIn('second-id',raw)
   rows=pipeline.decrypt_mappings(json.loads(raw));self.assertEqual(rows[0]['file_id'],'first-id');self.assertEqual(rows[0]['abyss_id'],'second-id')
if __name__=='__main__':unittest.main()
