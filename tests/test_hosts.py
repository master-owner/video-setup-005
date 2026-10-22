import contextlib,io,json,sys,unittest,os
from unittest.mock import patch
from pathlib import Path
from types import SimpleNamespace
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from clixarena_hosts import merge_provider_mapping,selected_host,upload_streamhg
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
  rows=merge_provider_mapping(rows,self.identity,'streamhg-id','streamhg')
  self.assertEqual(len(rows),1);self.assertEqual(rows[0]['file_id'],'stream-id');self.assertEqual(rows[0]['streamhg_id'],'streamhg-id');self.assertEqual(set(rows[0]['providers']),{'streamtape','streamhg'})
  rows=merge_provider_mapping(rows,self.identity,'new-streamhg','streamhg');self.assertEqual(rows[0]['file_id'],'stream-id');self.assertEqual(rows[0]['streamhg_id'],'new-streamhg')
 def test_streamhg_only_never_masquerades_as_streamtape_id(self):
  rows=merge_provider_mapping([],self.identity,'streamhg-id','streamhg');self.assertNotIn('file_id',rows[0]);self.assertEqual(rows[0]['provider'],'streamhg')
  rows=merge_provider_mapping(rows,self.identity,'stream-id');self.assertEqual(rows[0]['streamhg_id'],'streamhg-id')
 def test_deleted_streamtape_reference_preserves_streamhg_mapping(self):
  rows=merge_provider_mapping([],self.identity,'stream-id');rows=merge_provider_mapping(rows,self.identity,'streamhg-id','streamhg')
  result=prune(rows,{'stream-id'});self.assertEqual(result[0]['streamhg_id'],'streamhg-id');self.assertNotIn('file_id',result[0]);self.assertNotIn('streamtape',result[0]['providers'])
 def test_streamhg_upload_uses_secret_stdin_and_validates_documented_slug(self):
  calls=[]
  def run(args,**kw):calls.append((args,kw));return SimpleNamespace(returncode=0,stdout=json.dumps({'status':200,'files':[{'filecode':'abc123','status':'OK'}]}))
  with patch.object(hosts,'streamhg_api',return_value={'status':200,'result':'https://upload.example/upload/01'}):
   result=upload_streamhg(Path('clixarena.mp4'),'My title.mp4','secret-key-value',run=run,validate=lambda _:None)
  self.assertEqual(result,'abc123');self.assertEqual(len(calls),1);self.assertNotIn('secret-key-value',str(calls[0][0]));self.assertIn('key=secret-key-value',calls[0][1]['input']);self.assertIn('filename="My title.mp4"',calls[0][0][-1])
 def test_ambiguous_upload_is_not_retried(self):
  calls=[]
  def run(*args,**kw):calls.append(1);return SimpleNamespace(returncode=0,stdout='{"error":"failed"}')
  with patch.object(hosts,'streamhg_api',return_value={'status':200,'result':'https://upload.example/upload/01'}):
   with self.assertRaises(ValueError):upload_streamhg(Path('clixarena.mp4'),'x.mp4','secret-key-value',run=run,validate=lambda _:None)
  self.assertEqual(len(calls),1)
 def test_streamhg_completion_uses_only_selected_host_and_maps_its_id(self):
  calls=[];output=io.StringIO();path=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=123))
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"streamhg"}','GITHUB_OUTPUT':''}),patch.object(pipeline,'upload_streamhg',lambda *args,**kwargs:'private-streamhg-id'),patch.object(pipeline,'verify_streamhg',lambda *args:(True,True)),patch.object(pipeline,'save_mapping',lambda *args:calls.append(args)),patch.object(pipeline,'upload',side_effect=AssertionError('Wrong host')),contextlib.redirect_stdout(output):
   pipeline.complete_upload(path,'Title.mp4',{'STREAMHG_API_KEY':'secret'})
  self.assertEqual(calls,[('private-streamhg-id','streamhg')]);self.assertNotIn('private-streamhg-id',output.getvalue());self.assertNotIn('secret',output.getvalue())
 def test_pending_transcode_metadata_can_verify_upload(self):
  body={'status':200,'result':[{'file_code':'abc','status':200,'file_title':'Title','canplay':0}]}
  with patch.object(hosts,'streamhg_api',return_value=body):self.assertEqual(hosts.verify_streamhg('abc','secret',123,'Title.mp4',pause=lambda _:None),(True,True))
 def test_all_three_hosts_remain_selectable_and_preserved(self):
  old=[dict(self.identity,provider='abyss',abyss_id='old-id',providers={'abyss':{'file_id':'old-id'}})]
  rows=merge_provider_mapping(old,self.identity,'newid','streamhg')
  self.assertEqual(rows[0]['abyss_id'],'old-id');self.assertEqual(rows[0]['streamhg_id'],'newid')
  self.assertEqual(selected_host({'uploadHost':'abyss'}),'abyss')
  rows=merge_provider_mapping(rows,self.identity,'tapeid','streamtape')
  self.assertEqual(set(rows[0]['providers']),{'abyss','streamhg','streamtape'})
  self.assertEqual(rows[0]['file_id'],'tapeid')
 def test_bad_upload_server_is_rejected_before_key_is_sent(self):
  for url in ['http://example.com/upload/01','https://user:pass@example.com/upload/01','https://example.com:8443/upload/01','https://example.com/not-upload']:
   with patch.object(hosts,'streamhg_api',return_value={'status':200,'result':url}):
    with self.assertRaises(ValueError):upload_streamhg(Path('x.mp4'),'x.mp4','secret',run=lambda *a,**k:self.fail('Uploaded to unsafe endpoint'))
 def test_mapping_failure_after_accepted_upload_is_not_success_or_reupload(self):
  calls=[];path=SimpleNamespace(stat=lambda:SimpleNamespace(st_size=123))
  def upload(*args,**kwargs):calls.append(1);return 'private-streamhg-id'
  with patch.dict(os.environ,{'OVERLAY_LAYOUT':'{"uploadHost":"streamhg"}','GITHUB_OUTPUT':''}),patch.object(pipeline,'upload_streamhg',upload),patch.object(pipeline,'verify_streamhg',lambda *args:(True,True)),patch.object(pipeline,'save_mapping',side_effect=ValueError('private error')),contextlib.redirect_stdout(io.StringIO()):
   with self.assertRaises(pipeline.DownloadError):pipeline.complete_upload(path,'Title.mp4',{'STREAMHG_API_KEY':'secret'})
  self.assertEqual(len(calls),1)
if __name__=='__main__':unittest.main()
