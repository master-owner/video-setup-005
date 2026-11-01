import contextlib,io,os,unittest,urllib.error
from unittest.mock import patch
import clixarena_mapping_sync as sync
QUOTA = {
    'storageQuota': {'limit': 1000, 'actual': 100, 'usage': 10},
    'uploadQuota': {'dailyUploadQuota': 500, 'dailyUploadUsed': 50, 'dailyUploadRemaining': 450},
    'maxUploadSize': 2000
}

class AbyssAuthTests(unittest.TestCase):
    def test_login_uses_post_and_bearer_without_api_key_query(self):
        calls=[]
        def read(url,*args,**kwargs):
            calls.append((url,kwargs))
            if url.endswith('/auth/login'):return 200,{'token':'private-jwt','expiresIn':3600}
            return (200,QUOTA) if '/about' in url else (200,{'id':'video123'})
        with patch.dict(os.environ,{'ABYSS_EMAIL':'private@example.com','ABYSS_PASSWORD':'private-password','ABYSS_API_KEY':'private-key'}), \
             patch.object(sync,'read_json',side_effect=read),patch.object(sync,'mask') as mask,contextlib.redirect_stdout(io.StringIO()) as out:
            missing,failed=sync.collect_missing([{'abyss_id':'video123'}],lambda _:None)
        self.assertEqual(failed,[])
        self.assertEqual(calls[0][1]['payload'],{'email':'private@example.com','password':'private-password'})
        for url,kwargs in calls[1:]:
            self.assertNotIn('?',url);self.assertEqual(kwargs['headers'],{'Authorization':'Bearer private-jwt'})
        mask.assert_called_once_with('private-jwt')
        self.assertNotIn('private',out.getvalue())

    def test_partial_login_does_not_silently_fall_back(self):
        with patch.dict(os.environ,{'ABYSS_EMAIL':'email','ABYSS_PASSWORD':'','ABYSS_API_KEY':'key'}):
            with self.assertRaises(sync.SyncError):sync.abyss_auth()

    def test_static_bearer_token_and_api_key_modes(self):
        with patch.dict(os.environ,{'ABYSS_EMAIL':'','ABYSS_PASSWORD':'','ABYSS_AUTH_TOKEN':'token','ABYSS_API_KEY':'key'}),patch.object(sync,'mask'):
            self.assertEqual(sync.abyss_auth(),('',{'Authorization':'Bearer token'}))
            os.environ['ABYSS_AUTH_TOKEN']=''
            self.assertEqual(sync.abyss_auth(),('?key=key',{}))

    def test_http403_reports_stage_without_url_key_or_raw_body(self):
        error=urllib.error.HTTPError('https://api.abyss.to/v1/about?key=PRIVATE',403,'secret response',{},None)
        with patch.object(sync.urllib.request,'build_opener') as build:
            build.return_value.open.side_effect=error
            with self.assertRaises(sync.SyncError) as caught:
                sync.read_json('https://api.abyss.to/v1/about?key=PRIVATE',stage='Abyss quota lookup')
        self.assertEqual(str(caught.exception),'Abyss quota lookup: HTTP 403')

if __name__=='__main__':unittest.main()
