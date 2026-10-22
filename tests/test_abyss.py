import contextlib,io,json,sys,unittest,os
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from clixarena_hosts import merge_provider_mapping,selected_host,upload_abyss
import clixarena_hosts as hosts
from clixarena_mapping_sync import prune
import clixarena_video as pipeline
class HostTests(unittest.TestCase):
 identity={'type':'movie','tmdb_id':1,'season':0,'episode':0,'name':'Example'}
 def test_default_and_allowlist(self):
  self.assertEqual(selected_host({}),'streamtape')
  with self.assertRaises(ValueError):selected_host({'uploadHost':'https://untrusted'})
 def test_second_host_preserves_first_host_and_legacy_streamtape_id(self):
  rows=merge_provider_mapping([],self.identity,'stream-id')
  rows=merge_provider_mapping(rows,self.identity,'abyss-id','abyss')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['file_id'],'stream-id');self.assertEqual(rows[0]['abyss_id'],'abyss-id');self.assertEqual(set(rows[0]['providers']),{'streamtape','abyss'})
  rows=merge_provider_mapping(rows,self.identity,'new-abyss','abyss');self.assertEqual(rows[0]['file_id'],'stream-id');self.assertEqual(rows[0]['abyss_id'],'new-abyss')
 def test_abyss_only_never_masquerades_as_streamtape_id(self):
  rows=merge_provider_mapping([],self.identity,'abyss-id','abyss');self.assertNotIn('file_id',rows[0]);self.assertEqual(rows[0]['provider'],'abyss')
  rows=merge_provider_mapping(rows,self.identity,'stream-id');self.assertEqual(rows[0]['abyss_id'],'abyss-id')
 def test_deleted_streamtape_reference_preserves_abyss_mapping(self):
  rows=merge_provider_mapping([],self.identity,'stream-id');rows=merge_provider_mapping(rows,self.identity,'abyss-id','abyss')
  result=prune(rows,{'stream-id'});self.assertEqual(result[0]['abyss_id'],'abyss-id');self.assertNotIn('file_id',result[0]);self.assertNotIn('streamtape',result[0]['providers'])
 def test_abyss_upload_uses_secret_stdin_and_validates_documented_slug(self):
  calls=[]
  def run(args,**kw):calls.append((args,kw));return SimpleNamespace(returncode=0,stdout=json.dumps({'slug':'abc-123'}))
  result=upload_abyss(Path('clixarena.mp4'),'My title.mp4','secret-key-value',run=run)
  self.assertEqual(result,'abc-123');self.assertEqual(len(calls),1);self.assertNotIn('secret-key-value',str(calls[0][0]));self.assertIn('https://up.abyss.to/secret-key-value',calls[0][1]['input']);self.assertIn('filename="My title.mp4"',calls[0][0][-1])
 def test_ambiguous_upload_is_not_retried(self):
  calls=[]
  def run(*args,**kw):calls.append(1);return SimpleNamespace(returncode=0,stdout='{"error":"failed"}')
  with self.assertRaises(ValueError):upload_abyss(Path('clixarena.mp4'),'x.mp4','secret-key-value',run=run)
  self.assertEqual(len(calls),1)
 def test_abyss_completion_uses_only_selected_host_and_maps_its_id(self):
  calls=[];output=io.StringIO();path=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=123))
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"abyss"}','GITHUB_OUTPUT':''}),patch.object(pipeline,'upload_abyss',lambda *args:'private-abyss-id'),patch.object(pipeline,'verify_abyss',lambda *args:(True,True)),patch.object(pipeline,'save_mapping',lambda *args:calls.append(args)),patch.object(pipeline,'upload',side_effect=AssertionError('Wrong host')),contextlib.redirect_stdout(output):
   pipeline.complete_upload(path,'Title.mp4',{'ABYSS_API_KEY':'secret'})
  self.assertEqual(calls,[('private-abyss-id','abyss')]);self.assertNotIn('private-abyss-id',output.getvalue());self.assertNotIn('secret',output.getvalue())
 def test_pending_transcode_metadata_can_verify_upload(self):
  class Response:
   def __enter__(self):return self
   def __exit__(self,*args):pass
   def read(self,*args):return json.dumps({'id':'abc','size':123,'name':'Title.mp4','status':'waiting'}).encode()
  opener=SimpleNamespace(open=lambda *args,**kw:Response())
  with patch.object(hosts.urllib.request,'build_opener',lambda *args:opener):self.assertEqual(hosts.verify_abyss('abc','secret',123,'Title.mp4',pause=lambda _:None),(True,True))
 def test_mapping_failure_after_accepted_upload_is_not_success_or_reupload(self):
  calls=[];path=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=123))
  def upload(*args):calls.append(1);return 'private-abyss-id'
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"abyss"}','GITHUB_OUTPUT':''}),patch.object(pipeline,'upload_abyss',upload),patch.object(pipeline,'verify_abyss',lambda *args:(True,True)),patch.object(pipeline,'save_mapping',side_effect=ValueError('private error')),contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaises(pipeline.DownloadError):pipeline.complete_upload(path,'Title.mp4',{'ABYSS_API_KEY':'secret'})
  self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
