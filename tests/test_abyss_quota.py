import copy,os,unittest
from unittest.mock import patch
from urllib.parse import urlsplit,parse_qs
import clixarena_mapping_sync as sync
from test_mapping_sync_all import QUOTA

class AbyssQuotaTests(unittest.TestCase):
    def test_documented_quota_and_matching_file_succeed_with_query_api_key(self):
        calls=[]
        def read(url,allow_404=False,**kwargs):
            calls.append(url)
            return (200,QUOTA) if '/about?' in url else (200,{'id':'A-b_c','name':'Video'})
        with patch.dict(os.environ,{'ABYSS_API_KEY':'test&+key'}),patch.object(sync,'read_json',side_effect=read):
            missing,failed=sync.collect_missing([{'abyss_id':'A-b_c'}],lambda _:None)
        self.assertEqual(failed,[]);self.assertFalse(missing['abyss'])
        self.assertEqual([urlsplit(x).path for x in calls],['/v1/about','/v1/files/A-b_c'])
        for url in calls:self.assertEqual(parse_qs(urlsplit(url).query),{'key':['test&+key']})

    def test_bad_quota_stops_before_file_lookup(self):
        for field in ('storageQuota','uploadQuota','maxUploadSize'):
            body=copy.deepcopy(QUOTA);body.pop(field)
            self.assertFalse(sync.abyss_account_verified(body))
        body=copy.deepcopy(QUOTA);body['maxUploadSize']=True
        self.assertFalse(sync.abyss_account_verified(body))
        with patch.dict(os.environ,{'ABYSS_API_KEY':'test'}),patch.object(sync,'read_json',return_value=(200,{'id':'user','email':'u@example.com'})) as read:
            missing,failed=sync.collect_missing([{'abyss_id':'id'}],lambda _:None)
        self.assertEqual(read.call_count,1);self.assertEqual(failed,['abyss'])

    def test_successful_http_with_wrong_file_id_is_not_success(self):
        with patch.dict(os.environ,{'ABYSS_API_KEY':'test'}),patch.object(sync,'read_json',side_effect=[(200,QUOTA),(200,{'id':'wrong'})]):
            missing,failed=sync.collect_missing([{'abyss_id':'id'}],lambda _:None)
        self.assertEqual(failed,['abyss']);self.assertFalse(missing['abyss'])

    def test_unidentified_404_is_preserved(self):
        with patch.dict(os.environ,{'ABYSS_API_KEY':'test'}),patch.object(sync,'read_json',side_effect=[(200,QUOTA),(404,{'message':'Unknown provider error'})]):
            missing,failed=sync.collect_missing([{'abyss_id':'id'}],lambda _:None)
        self.assertEqual(failed,['abyss']);self.assertFalse(missing['abyss'])

if __name__=='__main__':unittest.main()
