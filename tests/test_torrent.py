import sys,unittest,os,json,base64,tempfile
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_torrent as t
class Files:
 def __init__(self,rows):self.rows=rows
 def num_files(self):return len(self.rows)
 def file_path(self,i):return self.rows[i][0]
 def file_size(self,i):return self.rows[i][1]
 def file_flags(self,i):return self.rows[i][2]
class Tests(unittest.TestCase):
 def test_candidates_ignores_nonvideo_and_rejects_paths_symlinks(self):
  info=NS(files=lambda:Files([('pack/ep1.mkv',100,0),('pack/readme.txt',10,0),('pack/ep2.mp4',200,0)]))
  self.assertEqual([f['index'] for f in t.candidates(info)],[0,2])
  for row in [('../evil.mkv',1,0),('/evil.mkv',1,0),('pack/evil.mkv',1,8)]:
   with self.assertRaises(t.TorrentError):t.candidates(NS(files=lambda:Files([row])))
 def test_same_run_selection_only(self):
  rows=[{'index':0,'name':'a.mkv','bytes':1},{'index':2,'name':'b.mp4','bytes':2}];writes=[];state={};clock=[0]
  def request(job,payload=None,sha=None,state_dir=''):
   self.assertEqual(state_dir,'torrent')
   if payload is not None:state.update(payload);writes.append(dict(payload));return {}
   return {'sha':'sha','content':base64.b64encode(json.dumps(state).encode()).decode()}
  def pause(seconds):clock[0]+=seconds;state.update(status='selected',selected=2)
  with patch.dict(os.environ,{'PORTAL_JOB_ID':'a'*32,'GITHUB_RUN_ID':'12'}):
   self.assertEqual(t.select_file(rows,request,pause=pause,clock=lambda:clock[0]),2)
  self.assertEqual(writes[-1]['status'],'downloading')
 def test_single_file_does_not_pause(self):self.assertEqual(t.select_file([{'index':3}],lambda *a:(_ for _ in ()).throw(Exception())),3)
 def test_cross_run_is_rejected(self):
  def request(job,payload=None,sha=None,state_dir=''):
   return {} if payload is not None else {'sha':'x','content':base64.b64encode(json.dumps({'job':'other','run_id':'99'}).encode()).decode()}
  with patch.dict(os.environ,{'PORTAL_JOB_ID':'a'*32,'GITHUB_RUN_ID':'12'}),self.assertRaises(t.TorrentError):t.select_file([{'index':0},{'index':1}],request,pause=lambda s:None)
 def test_download_prioritizes_one_file_and_moves_only_verified_video(self):
  rows=[('pack/ep1.mkv',100,0),('pack/readme.txt',30,0),('pack/ep2.mp4',200,0)]
  info=NS(files=lambda:Files(rows),num_files=lambda:3,priv=lambda:False)
  priorities=[];renames={};removed=[]
  class Handle:
   def has_metadata(self):return True
   def torrent_file(self):return info
   def prioritize_files(self,p):priorities.append(p)
   def rename_file(self,i,path):renames[i]=path
   def unset_flags(self,flags):
    self.asserted=True
    Path('.torrent-payload',renames[2]).write_bytes(b'x'*200)
   def status(self):return NS(errc=NS(value=lambda:0),download_rate=200,num_peers=1)
   def file_progress(self,flag):
    if flag!=1:raise AssertionError('Unverified blocks must not count')
    return [0,0,200]
   def pause(self):pass
   def force_reannounce(self):pass
   def force_dht_announce(self):pass
  h=Handle()
  class Session:
   def __init__(self,settings):pass
   def set_ip_filter(self,rules):pass
   def add_torrent(self,params):return h
   def pop_alerts(self):return []
   def remove_torrent(self,handle):removed.append(handle)
   def pause(self):pass
  fake=NS(session=Session,torrent_flags=NS(upload_mode=4),torrent_handle=NS(piece_granularity=1))
  previous=Path.cwd()
  with tempfile.TemporaryDirectory(dir='.') as tmp:
   try:
    os.chdir(tmp)
    with patch.dict(sys.modules,{'libtorrent':fake}),patch.object(t,'public_filter',return_value=None),patch.object(t,'prepare',return_value=NS(ti=info)),patch.object(t,'select_file',return_value=2),patch.object(t.shutil,'disk_usage',return_value=NS(free=20*1024**3)):
     t.download_torrent({'kind':'file'},Path('source.video'),1024,Path.cwd(),None)
    self.assertEqual(priorities,[[0,0,0],[0,0,4]])
    self.assertEqual(Path('source.video').read_bytes(),b'x'*200)
    self.assertFalse(Path('.torrent-payload',renames[0]).exists());self.assertEqual(len(removed),1)
   finally:os.chdir(previous)
 def test_trackers_allow_public_udp_but_block_private_http_and_credentials(self):
  def resolver(host,port,**kwargs):return [(2,2,17,'',('127.0.0.1' if host=='private.test' else '8.8.8.8',port))]
  values=['udp://public.test:1337/announce','udp://private.test:1337/announce','https://public.test/announce','udp://user:pass@public.test:1337/announce','udp://public.test:1337/announce?passkey=secret']
  result=t.public_trackers(values,resolver)
  self.assertIn(values[0],result)
  for value in values[1:]:self.assertNotIn(value,result)
 def test_discovery_diagnostics_never_echo_raw_tracker_errors(self):
  error=type('tracker_error_alert',(),{'message':lambda self:'SECRET-URL'})()
  reply=type('tracker_reply_alert',(),{})()
  session=NS(pop_alerts=lambda:[error,reply],status=lambda:NS(dht_nodes=42));counters={'errors':0,'replies':0}
  output=t.discovery(session,None,None,counters)
  self.assertIn('DHT nodes 42',output);self.assertIn('tracker replies 1',output);self.assertNotIn('SECRET',output)
if __name__ == "__main__": unittest.main()
