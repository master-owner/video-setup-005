import sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from clixarena_mapping_sync import missing_ids,confirmed_missing,prune
class MappingSyncTests(unittest.TestCase):
 def test_only_explicit_per_file_404(self):
  self.assertEqual(missing_ids(['a','b','c','d','e'],{'a':{'status':404},'b':{'status':403},'c':{'status':451},'d':{'status':500},'e':{'status':'404'}}),{'a'})
  self.assertEqual(missing_ids(['a'],{}),set())
 def test_requires_two_confirmations(self):
  answers=iter([{'a':{'status':404},'b':{'status':404}},{'a':{'status':200},'b':{'status':404}}])
  self.assertEqual(confirmed_missing(['a','b'],lambda ids:next(answers),lambda _:None),{'b'})
 def test_preserves_other_fields_and_new_uploads(self):
  rows=[{'file_id':'old','tmdb_id':1,'subtitle_url':'secret'},{'file_id':'new','tmdb_id':1},{'name':'unknown'}]
  self.assertEqual(prune(rows,{'old'}),rows[1:])
 def test_service_failure_is_not_missing(self):
  with self.assertRaises(TimeoutError):confirmed_missing(['a'],lambda ids:(_ for _ in ()).throw(TimeoutError()),lambda _:None)
if __name__=='__main__':unittest.main()
