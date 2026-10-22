import base64,copy,io,json,os,unittest,contextlib
from unittest.mock import patch,Mock
import clixarena_mapping_sync as sync
from clixarena_mapping_restore import restore_streamhg

class RecoveryTests(unittest.TestCase):
    def row(self,id=1,**kwargs):return dict(type='movie',tmdb_id=id,season=0,episode=0,**kwargs)

    def test_restore_only_lost_references_preserving_concurrent_uploads(self):
        before=[self.row(streamhg_id='oldhg',file_id='tape'),self.row(2,streamhg_id='deletedrow',name='Old')]
        after=[self.row(file_id='tape')]
        current=[self.row(file_id='newtape',name='New',subtitle_url='https://example/sub.srt'),self.row(3,abyss_id='newvideo')]
        result,count=restore_streamhg(before,after,current)
        self.assertEqual(count,2)
        self.assertEqual(result[0]['file_id'],'newtape');self.assertEqual(result[0]['name'],'New')
        self.assertEqual(result[0]['streamhg_id'],'oldhg');self.assertEqual(result[1],current[1])
        self.assertEqual(result[2]['providers'],{'streamhg':{'file_id':'deletedrow'}})
        self.assertEqual(restore_streamhg(before,after,result)[1],0)

    def test_new_streamhg_id_and_untouched_old_missing_rows_are_preserved(self):
        before=[self.row(streamhg_id='oldhg')];after=[self.row()];current=[self.row(streamhg_id='newhg')]
        self.assertEqual(restore_streamhg(before,after,current),(current,0))
        self.assertEqual(restore_streamhg([],[],current),(current,0))

    def test_bulk_missing_never_passes(self):
        with self.assertRaises(sync.SyncError):sync.bulk_guard(17,set(map(str,range(17))))
        with self.assertRaises(sync.SyncError):sync.bulk_guard(20,set(map(str,range(5))))
        sync.bulk_guard(100,{'one'})

    def test_file_info_alone_does_not_remove_streamhg(self):
        def api(route,*args):
            if route=='account/info':return {'status':200,'result':{'login':'account'}}
            if route=='file/info':return {'status':200,'result':[{'file_code':'hg','status':404}]}
            return {'status':200,'result':[]}
        with patch.dict(os.environ,{'STREAMHG_API_KEY':'key'}),patch('clixarena_hosts.streamhg_api',side_effect=api):
            missing,failed=sync.collect_missing([self.row(streamhg_id='hg')],lambda _:None)
        self.assertEqual(missing['streamhg'],set());self.assertIn('streamhg',failed)

    def test_any_failed_host_blocks_all_writes(self):
        response=Mock();response.__enter__=Mock(return_value=response);response.__exit__=Mock(return_value=False)
        response.read.return_value=json.dumps({'sha':'current','content':base64.b64encode(b'[]').decode()}).encode()
        opener=Mock();opener.open.return_value=response
        with patch.dict(os.environ,{'GITHUB_REPOSITORY':'owner/repo','GITHUB_REF_NAME':'main','MAPPING_GITHUB_TOKEN':'test'}), \
             patch.object(sync.urllib.request,'build_opener',return_value=opener),patch.object(sync,'decrypt_mappings',return_value=[self.row(file_id='tape')]), \
             patch.object(sync,'collect_missing',return_value=({'streamtape':{'tape'}},['abyss'])),patch.object(sync,'mask'),contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(sync.main(),1)
        self.assertEqual(opener.open.call_count,1)
        self.assertEqual(opener.open.call_args.args[0].method,'GET')

if __name__=='__main__':unittest.main()
