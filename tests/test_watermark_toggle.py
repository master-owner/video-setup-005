import os,sys,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import clixarena_features as f
class WatermarkTests(unittest.TestCase):
 def test_default_true_false_and_strict_validation(self):
  for raw,expected in [('{}',True),('{"watermarkEnabled":false}',False),('{"watermarkEnabled":true}',True)]:
   with patch.dict(os.environ,{'OVERLAY_LAYOUT':raw,'TITLE_ANIMATION':'false'}):self.assertEqual(f.settings()['watermark_enabled'],expected)
  for raw in ('{"watermarkEnabled":"false"}','{"watermarkEnabled":0}','{"watermarkEnabled":null}'):
   with patch.dict(os.environ,{'OVERLAY_LAYOUT':raw}):
    with self.assertRaises(ValueError):f.settings()
 def test_off_keeps_burn_subtitle_and_title_filters_without_invalid_commas(self):
  options={'watermark_enabled':False,'subtitle_enabled':True,'parts':{'name':True,'message':False},'content_size':(1280,532)}
  graph=f.filter_graph(1280,720,options)
  self.assertNotIn('drawtext=',graph);self.assertIn('ass=subtitles.ass',graph);self.assertIn('ass=intro.ass',graph);self.assertIn('pad=1280:720',graph);self.assertNotIn(',[',graph)
if __name__=='__main__':unittest.main()
