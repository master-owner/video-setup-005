import copy
import os
import unittest
from unittest.mock import patch
import clixarena_mapping_sync as sync

QUOTA={'storageQuota':{'limit':1125899906842624,'actual':1099511627776,'usage':0},'uploadQuota':{'dailyUploadQuota':107374182400,'dailyUploadUsed':0,'dailyUploadRemaining':107374182400},'maxUploadSize':10737418240,'perms':{'reconvert':False}}

ROWS=[{'type':'movie','tmdb_id':1,'name':'Keep name','subtitle_url':'https://example.com/a.srt',
       'file_id':'tape1','abyss_id':'abyss1','streamhg_id':'hg1','provider':'streamhg',
       'providers':{'streamtape':{'file_id':'tape1'},'abyss':{'file_id':'abyss1'},'streamhg':{'file_id':'hg1'}}}]

class SyncTests(unittest.TestCase):
    def test_prune_only_selected_host_and_preserve_metadata(self):
        original=copy.deepcopy(ROWS)
        result=sync.prune(ROWS,{'streamhg':{'hg1'}})
        self.assertEqual(ROWS,original)
        self.assertEqual(result[0]['subtitle_url'],ROWS[0]['subtitle_url'])
        self.assertEqual(result[0]['name'],'Keep name')
        self.assertNotIn('streamhg_id',result[0])
        self.assertNotIn('streamhg',result[0]['providers'])
        self.assertEqual(set(result[0]['providers']),{'streamtape','abyss'})
        self.assertEqual(result[0]['provider'],'streamtape')

    def test_delete_title_only_when_all_sources_missing(self):
        self.assertEqual(sync.prune(ROWS,{'streamtape':{'tape1'},'abyss':{'abyss1'},'streamhg':{'hg1'}}),[])
        self.assertEqual(sync.prune(ROWS,{}),ROWS)

    def test_current_and_legacy_formats_and_namespaced_ids(self):
        row={'type':'movie','tmdb_id':2,'abyss_id':'same','streamhg_id':'same'}
        self.assertEqual(sync.prune([row],{'abyss':{'same'}})[0]['providers'],{'streamhg':{'file_id':'same'}})
        self.assertEqual(sync.host_ids({'providers':{'abyss':{'file_id':'A_-'}}}),{'abyss':'A_-'})

    def test_streamhg_only_explicit_individual_404_counts(self):
        body={'status':200,'result':[{'file_code':'a','status':404},{'file_code':'b','status':200},
                                    {'file_code':'c','status':'404'},{'file_code':'other','status':404}]}
        self.assertEqual(sync.streamhg_missing(['a','b','c','omitted'],body),{'a'})
        with self.assertRaises(ValueError):sync.streamhg_missing(['a'],{'status':403})

    def test_abyss_generic_404_and_auth_errors_never_delete(self):
        self.assertFalse(sync.abyss_file_missing('a',404,{'message':'Internal error'}))
        self.assertFalse(sync.abyss_file_missing('a',401,{'message':'File not found'}))
        self.assertFalse(sync.abyss_file_missing('a',404,{'message':'File not found','id':'other'}))
        self.assertTrue(sync.abyss_file_missing('a',404,{'message':'File not found'}))
        self.assertTrue(sync.abyss_file_missing('a',404,{'message':'Not Found'}))
        self.assertTrue(sync.abyss_file_missing('a',404,{'message':'File not found','id':'a'}))
        self.assertFalse(sync.abyss_account_verified({'status':200}))
        self.assertFalse(sync.abyss_account_verified({'user':{'id':'user','email':'test@example.com'}}))
        self.assertTrue(sync.abyss_account_verified(QUOTA))

    def test_all_providers_confirm_twice(self):
        def read(url,allow_404=False,**kwargs):
            return (200,QUOTA) if '/about' in url else (404,{'message':'File not found','id':'abyss1'})
        with patch.dict(os.environ,{'STREAMTAPE_LOGIN':'login','STREAMTAPE_KEY':'key','STREAMHG_API_KEY':'key','ABYSS_API_KEY':'key'}), \
             patch.object(sync,'api',return_value={'tape1':{'status':404}}) as tape, \
             patch('clixarena_hosts.streamhg_api',side_effect=lambda route,*args: {'status':200,'result':{'login':'user'}} if route=='account/info' else {'status':200,'result':[{'file_code':'hg1','status':404}]} if route=='file/info' else {'status':200,'result':[{'file_code':'hg1','deleted':'2026-10-21','deleted_by':'me'}]}) as hg, \
             patch.object(sync,'read_json',side_effect=read) as abyss:
            missing,failed=sync.collect_missing(ROWS,pause=lambda _:None)
        self.assertEqual(failed,[])
        self.assertEqual(missing,{'streamtape':{'tape1'},'abyss':{'abyss1'},'streamhg':{'hg1'}})
        self.assertEqual((tape.call_count,hg.call_count,abyss.call_count),(2,5,3))

    def test_auth_failure_preserves_host_while_other_hosts_sync(self):
        with patch.dict(os.environ,{'STREAMTAPE_LOGIN':'login','STREAMTAPE_KEY':'key','STREAMHG_API_KEY':'key','ABYSS_API_KEY':'key'}), \
             patch.object(sync,'api',return_value={'tape1':{'status':404}}), \
             patch('clixarena_hosts.streamhg_api',side_effect=TimeoutError('private-secret')), \
             patch.object(sync,'read_json',return_value=(200,{'error':'unauthorized'})):
            missing,failed=sync.collect_missing(ROWS,pause=lambda _:None)
        self.assertEqual(set(failed),{'abyss','streamhg'})
        self.assertEqual(missing['abyss'],set())
        self.assertEqual(set(sync.prune(ROWS,missing)[0]['providers']),{'abyss','streamhg'})

    def test_transient_missing_on_second_check_is_preserved(self):
        replies=iter([{'a':{'status':404}},{'a':{'status':200}}])
        self.assertEqual(sync.confirmed_missing(['a'],lambda _:next(replies),lambda _:None),set())

if __name__=='__main__':unittest.main()
