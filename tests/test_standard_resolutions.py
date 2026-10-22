import os,sys,unittest,tempfile,subprocess,shutil,json
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_features as f
class StandardResolutionTests(unittest.TestCase):
 def test_canvas_choices_and_original(self):
  stream={'width':1282,'height':534}
  for height,canvas in {360:(640,360),480:(854,480),720:(1280,720),1080:(1920,1080),1440:(2560,1440),2160:(3840,2160)}.items():
   self.assertEqual(f.video_dimensions(stream,height),canvas)
   with patch.dict(os.environ,{'QUALITY':str(height)+'p - Balanced','TITLE_ANIMATION':'false','OVERLAY_LAYOUT':'{}','CRF':'auto'}):self.assertEqual(f.settings()['height'],height)
  self.assertEqual(f.video_dimensions(stream,None),(1282,534))
 def test_fit_preserves_wide_portrait_anamorphic_and_rotated_display(self):
  for stream in ({'width':1282,'height':534},{'width':534,'height':1282},{'width':720,'height':576,'sample_aspect_ratio':'16:15'},{'width':1280,'height':720,'side_data_list':[{'rotation':90}]}):
   original=f.video_dimensions(stream,None);fit=f.fitted_dimensions(stream,1280,720)
   self.assertLessEqual(fit[0],1280);self.assertLessEqual(fit[1],720)
   self.assertLess(abs(fit[0]-fit[1]*original[0]/original[1]),5)
   self.assertEqual(fit[0]%2,0);self.assertEqual(fit[1]%2,0)
 @unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'),'FFmpeg required')
 def test_real_encoder_outputs_exact_frame_with_black_bars_and_uncropped_picture(self):
  for source in ((1282,534),(534,1282),(640,480)):
   options={'subtitle_enabled':False,'parts':{'name':False,'message':False},'content_size':f.fitted_dimensions({'width':source[0],'height':source[1]},1280,720)}
   graph=f.filter_graph(1280,720,options)
   vf=graph.split(',drawtext=',1)[0].removeprefix('[0:v:0]')
   with tempfile.TemporaryDirectory() as tmp:
    target=Path(tmp)/'frame.mp4'
    result=subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color=c=red:s={source[0]}x{source[1]}:r=1','-vf',vf,'-frames:v','1','-c:v','libx264','-preset','ultrafast','-pix_fmt','yuv420p',str(target)],capture_output=True)
    self.assertEqual(result.returncode,0,result.stderr.decode(errors='replace'))
    info=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-of','json',str(target)]))['streams'][0]
    self.assertEqual((info['width'],info['height']),(1280,720));self.assertEqual(info['sample_aspect_ratio'],'1:1')
    raw=subprocess.check_output(['ffmpeg','-v','error','-i',str(target),'-frames:v','1','-pix_fmt','rgb24','-f','rawvideo','-'])
    self.assertLess(max(raw[:3]),20)
    center=(360*1280+640)*3;self.assertGreater(raw[center],200);self.assertLess(raw[center+1],30)
if __name__=='__main__':unittest.main()
