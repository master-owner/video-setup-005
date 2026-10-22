import contextlib,io,subprocess,sys,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_upload_metrics as m

class UploadMetricsTests(unittest.TestCase):
 def test_progress_parser_only_accepts_meter_rows(self):
  self.assertEqual(m.progress_row(' 50 20M 0 0 50 10M 0 1024k 0:00:20 0:00:10 0:00:10 1024k'),(50,10*1024**2))
  for text in ['curl: (22) https://private/?key=SECRET','PRIVATE VIDEO-ID','100 bad 0 0 150 5M 0 2M -- -- -- 2M']:
   self.assertIsNone(m.progress_row(text))
 def test_metrics_do_not_leak_response_or_credentials_and_keep_http_marker(self):
  calls=[];output=io.StringIO()
  def run(args,**kw):
   calls.append((args,kw));return SimpleNamespace(returncode=0,stdout='{"slug":"PRIVATEID"}\nCLIXARENA_HTTP:200'+m._STATS+'1048576|524288|2|0.1|200',stderr='https://private/?key=SECRET')
  with contextlib.redirect_stdout(output):
   result=m.run_upload(['curl','--config','-','--silent','--write-out','\nCLIXARENA_HTTP:%{http_code}','--form','file=@x.mp4'],input='url="https://private/SECRET"',runner=run,label='StreamHG',size=1048576)
  self.assertEqual(result.stdout,'{"slug":"PRIVATEID"}\nCLIXARENA_HTTP:200');self.assertEqual(len(calls),1)
  self.assertIn('--expect100-timeout',calls[0][0]);self.assertEqual(calls[0][0][calls[0][0].index('--expect100-timeout')+1],'0.2')
  self.assertIn('average 0.50 MiB/s',output.getvalue());self.assertIn('transfer/request 2.0s',output.getvalue())
  for secret in ['PRIVATEID','SECRET','https://private']:self.assertNotIn(secret,output.getvalue())
 def test_real_subprocess_stderr_is_private_and_live_progress_is_numeric(self):
  output=io.StringIO()
  with tempfile.TemporaryDirectory() as tmp:
   script=Path(tmp)/'fake_uploader.py'
   script.write_text("import sys\nsys.stdin.read()\nsys.stderr.write('https://private/SECRET PRIVATEID\\r')\nsys.stderr.write('50 20M 0 0 50 10M 0 1024k 0:00:20 0:00:10 0:00:10 1024k\\r');sys.stderr.flush()\nprint('{\\\"slug\\\":\\\"PRIVATEID\\\"}'+ '\\nCLIXARENA_UPLOAD_STATS:1048576|524288|2|0.1|200')\n",encoding='utf-8')
   clock=[0]
   def tick():clock[0]+=11;return clock[0]
   with patch.object(m.time,'monotonic',side_effect=tick),contextlib.redirect_stdout(output):
    result=m.run_upload([sys.executable,str(script)],input='private-config',label='Abyss',size=20*1024**2)
  self.assertEqual(result.returncode,0);self.assertIn('Abyss upload 50%',output.getvalue())
  self.assertNotIn('SECRET',output.getvalue());self.assertNotIn('PRIVATEID',output.getvalue());self.assertNotIn('https://',output.getvalue())
 def test_stage_timer_does_not_print_exception_contents(self):
  output=io.StringIO()
  with contextlib.redirect_stdout(output):
   with self.assertRaises(ValueError):
    m.timed_call('Abyss metadata verification',lambda:(_ for _ in ()).throw(ValueError('https://SECRET/PRIVATEID')))
  self.assertIn('failed:',output.getvalue());self.assertNotIn('SECRET',output.getvalue())
 def test_failed_upload_is_not_repeated(self):
  calls=[]
  def failed(args,**kwargs):calls.append(1);return SimpleNamespace(returncode=22,stdout='',stderr='SECRET')
  with contextlib.redirect_stdout(io.StringIO()):result=m.run_upload(['curl'],input='private-config',runner=failed,label='Streamtape')
  self.assertEqual(result.returncode,22);self.assertEqual(calls,[1]);self.assertEqual(result.stderr,'')

if __name__=='__main__':unittest.main()
