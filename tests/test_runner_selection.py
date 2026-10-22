import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from clixarena_video import selected_runner_mode

class RunnerSelectionTests(unittest.TestCase):
 def test_ui_parallel_overrides_github_default_single(self):
  self.assertEqual(selected_runner_mode({'runnerMode':'parallel'},'single'),'parallel')
 def test_ui_single_is_not_forced_to_parallel(self):
  self.assertEqual(selected_runner_mode({'runnerMode':'single'},'parallel'),'single')
 def test_manual_workflow_selection_and_default(self):
  self.assertEqual(selected_runner_mode({},'parallel'),'parallel')
  self.assertEqual(selected_runner_mode({},'single'),'single')
  self.assertEqual(selected_runner_mode({},''),'single')
 def test_invalid_selection_fails_instead_of_silently_using_single(self):
  for value in [False,[],{},'unknown']:
   with self.assertRaises(ValueError):selected_runner_mode({'runnerMode':value},'single')
 def test_workflow_keeps_portal_selection_first(self):
  text=(Path(__file__).resolve().parents[1]/'.github/workflows/clixarena-video.yml').read_text(encoding='utf-8')
  self.assertIn("RUNNER_MODE: ${{ fromJSON(inputs.overlay_layout || '{}').runnerMode || inputs.runner_mode || 'single' }}",text)

if __name__=='__main__':unittest.main()
