import io
import sys
import threading
import unittest
import uuid
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clixarena_parallel as downloader


class Response(io.BytesIO):
    def __init__(self, body, status, headers):
        super().__init__(body)
        self.status = status
        self.headers = headers


class ParallelTests(unittest.TestCase):
    def setUp(self):
        self.root = Path(__file__).resolve().parent / ('parallel-' + uuid.uuid4().hex)
        self.root.mkdir()
        self.target = self.root / 'video.bin'
        self.data = bytes(range(256)) * 4097
        self.requests = []
        self.messages = []

    def tearDown(self):
        shutil.rmtree(self.root)

    def run_download(self, mode='ok', compatibility=False, connections=4):
        barrier = threading.Barrier(connections if type(connections) is int and 1<=connections<=16 else 4)
        data = self.data
        attempts = {}
        lock = threading.Lock()
        def open_request(request, timeout):
            headers = dict((k.lower(), v) for k, v in request.header_items())
            self.requests.append(headers)
            first, last = map(int, headers['range'][6:].split('-'))
            with lock:
                attempts[(first,last)] = attempts.get((first,last),0)+1
                attempt = attempts[(first,last)]
            if last-first+1 > 1 and (mode == 'always-full' or mode == 'transient-full' and attempt == 1):
                return Response(data, 200, {'ETag':'"v1"'})
            if mode == 'probe-transient' and headers['range']=='bytes=0-0' and attempt==1:
                return Response(data,200,{})
            if mode == 'ignore':
                return Response(data, 200, {})
            if mode == 'large':
                return Response(b'x', 206, {'Content-Range': 'bytes 0-0/999999999999', 'ETag': '"v1"'})
            body = data[first:last+1]
            if mode == 'mutated' and len(self.requests) > 10:
                body = bytes([body[0] ^ 1]) + body[1:]
            etag = '"v1"'
            if last-first+1 > 65536:
                if mode != 'small-chunks':barrier.wait(timeout=5)  # proves simultaneous requests
                if mode == 'changed' or mode == 'dynamic':
                    etag = '"v2"'
                if mode == 'short':
                    body = body[:-1]
            result = {'Content-Range': f'bytes {first}-{last}/{len(data)}',
                      'Content-Length': str(last-first+1), 'ETag': etag}
            if mode == 'no-validator':
                result.pop('ETag')
            return Response(body, 206, result)
        with patch.object(downloader, 'MIN_SIZE', 1), patch.object(downloader.shutil, 'disk_usage', return_value=SimpleNamespace(free=10**12)):
            return downloader._parallel_download_once(('https://trendimovies.com/tgstream/stream/1' if compatibility else 'https://example.test/private?token=secret'), self.target, 12*1024**3,
                lambda: SimpleNamespace(open=open_request), lambda url: None,
                log=lambda text, **kwargs: self.messages.append(text), connections=connections)

    def test_parallel_bytes_and_if_range(self):
        self.assertTrue(self.run_download())
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertEqual(len(self.requests), 5)
        self.assertTrue(all(r.get('if-range') == '"v1"' for r in self.requests[1:]))
        self.assertNotIn('secret', ' '.join(self.messages))

    def test_unsupported_server_falls_back_without_body_download(self):
        self.assertFalse(self.run_download('ignore'))
        self.assertFalse(self.target.exists())
        self.assertEqual(len(self.requests), 3)

    def test_size_limit_falls_back(self):
        for mode in ('large',):
            with self.subTest(mode=mode):
                self.assertFalse(self.run_download(mode))
                self.assertFalse(self.target.exists())

    def test_missing_validator_uses_sample_verified_parallel_download(self):
        self.assertTrue(self.run_download('no-validator'))
        self.assertEqual(self.target.read_bytes(),self.data)
        self.assertTrue(all('if-range' not in r for r in self.requests))

    def test_changed_file_or_truncated_range_is_discarded(self):
        for mode in ('changed', 'short'):
            with self.subTest(mode=mode):
                self.assertFalse(self.run_download(mode))
                self.assertFalse(self.target.exists())
                self.assertIn('Parallel attempt failed; partial download discarded', self.messages)

    def test_compatibility_accepts_dynamic_validators(self):
        self.assertTrue(self.run_download('dynamic', compatibility=True))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertTrue(all('if-range' not in r for r in self.requests))

    def test_compatibility_rejects_truncated_segments(self):
        self.assertFalse(self.run_download('short', compatibility=True))
        self.assertFalse(self.target.exists())

    def test_compatibility_rejects_changed_content_samples(self):
        self.assertFalse(self.run_download('mutated', compatibility=True))
        self.assertFalse(self.target.exists())

    def test_selected_connection_count(self):
        for count in (2, 8, 16):
            self.requests.clear()
            self.assertTrue(self.run_download(connections=count))
            self.assertEqual(self.target.read_bytes(), self.data)
            self.assertEqual(len(self.requests), count+1)

    def test_single_connection_bypasses_parallel(self):
        self.assertFalse(self.run_download(connections=1))
        self.assertEqual(self.requests, [])

    def test_invalid_counts_rejected(self):
        for count in (0, 17, 2.5, True, '4'):
            with self.assertRaises(ValueError):
                self.run_download(connections=count)

    def test_selected_six_connections_recovers_ignored_range(self):
        self.assertTrue(self.run_download('transient-full', connections=6))
        self.assertEqual(self.target.read_bytes(), self.data)
        self.assertTrue(any('if-range' not in r and r['range'] != 'bytes=0-0' for r in self.requests))
        self.assertLessEqual(len(self.requests), 13)

    def test_permanently_ignored_ranges_fall_back(self):
        self.assertFalse(self.run_download('always-full', connections=6))
        self.assertFalse(self.target.exists())
        self.assertLessEqual(len(self.requests), 19)

    def test_small_chunks_are_assembled_without_changing_bytes(self):
        with patch.object(downloader, 'CHUNK_SIZE', 100000):
            self.assertTrue(self.run_download('small-chunks', connections=6))
        self.assertEqual(self.target.read_bytes(), self.data)
        for r in self.requests[1:]:
            first,last=map(int,r['range'][6:].split('-'))
            self.assertLessEqual(last-first+1,100000)

    def test_transient_full_probe_recovers_before_parallel_download(self):
        self.assertTrue(self.run_download('probe-transient', connections=6))
        self.assertEqual(self.target.read_bytes(),self.data)
        self.assertIn('Range probe returned full response; retrying probe',self.messages)
        self.assertEqual(len(self.requests),8)

    def test_early_fallback_reason_is_visible_without_url(self):
        self.assertFalse(self.run_download('ignore'))
        self.assertIn('Parallel fallback reason: server did not return a partial response after range probing',self.messages)
        self.assertNotIn('secret',' '.join(self.messages))

    def test_explicit_disable(self):
        with patch.dict('os.environ', {'PARALLEL_DOWNLOAD': 'off'}):
            self.assertFalse(self.run_download())
        self.assertEqual(self.requests, [])


class StepDownTests(unittest.TestCase):
    def test_six_five_four_then_success_at_three(self):
        attempts=[];logs=[]
        def attempt(*args):
            count=args[-1];attempts.append(count)
            return True if count==3 else None
        with patch.object(downloader,'_parallel_download_once',side_effect=attempt):
            self.assertTrue(downloader.parallel_download('private',Path('unused'),100,None,None,log=lambda text,**kw:logs.append(text),connections=6))
        self.assertEqual(attempts,[6,5,4,3])
        self.assertNotIn('Parallel download unavailable; using a single connection',logs)

    def test_single_stream_only_after_two_also_fails(self):
        attempts=[];logs=[]
        def attempt(*args):attempts.append(args[-1]);return None
        with patch.object(downloader,'_parallel_download_once',side_effect=attempt):
            self.assertFalse(downloader.parallel_download('private',Path('unused'),100,None,None,log=lambda text,**kw:logs.append(text),connections=6))
        self.assertEqual(attempts,[6,5,4,3,2])
        self.assertEqual(logs[-1],'Parallel download unavailable; using a single connection')

    def test_unsupported_ranges_do_not_repeat_meaningless_attempts(self):
        with patch.object(downloader,'_parallel_download_once',return_value=False) as once:
            self.assertFalse(downloader.parallel_download('private',Path('unused'),100,None,None,connections=6))
        once.assert_called_once()

    def test_single_selected_and_disabled_do_not_step_down(self):
        for count,disabled in [(1,'false'),(6,'off')]:
            with patch.dict('os.environ',{'PARALLEL_DOWNLOAD':disabled}),patch.object(downloader,'_parallel_download_once',return_value=False) as once:
                self.assertFalse(downloader.parallel_download('private',Path('unused'),100,None,None,connections=count))
                once.assert_called_once()

if __name__ == '__main__':
    unittest.main()
