import asyncio
import sys
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clixarena_hosts as hosts


class CryptoExecutorTests(unittest.TestCase):
    def test_loop_exists_before_setup_and_is_cleared_for_reused_threads(self):
        loops = []
        def setup(*args):
            loop = asyncio.get_event_loop()
            self.assertFalse(loop.is_closed())
            loops.append(loop)
            return '123'
        def worker():
            asyncio.set_event_loop(None)
            for _ in range(2):
                self.assertEqual(hosts._upload_telegram_single(None, None, None, None, None, None, None, None), '123')
                with self.assertRaises(RuntimeError):
                    asyncio.get_event_loop()
        with patch.object(hosts, '_upload_telegram_single_impl', side_effect=setup), ThreadPoolExecutor(1) as uploads:
            uploads.submit(worker).result()
        self.assertEqual(len({id(loop) for loop in loops}), 2)
        self.assertTrue(all(loop.is_closed() for loop in loops))

    def test_existing_native_executor_is_preserved(self):
        with ThreadPoolExecutor(1) as native:
            module = SimpleNamespace(crypto_executor=native)
            with patch.object(hosts, '_pyrogram', module):
                self.assertIs(hosts._ensure_crypto_executor(), native)

    def test_parallel_upload_threads_share_one_working_executor(self):
        module = SimpleNamespace()
        gate = threading.Barrier(8)
        def worker(_):
            gate.wait()
            executor = hosts._ensure_crypto_executor()
            async def encrypt_work():
                return await asyncio.get_running_loop().run_in_executor(executor, lambda: 42)
            return executor, asyncio.run(encrypt_work())
        try:
            with patch.object(hosts, '_pyrogram', module), ThreadPoolExecutor(8) as uploads:
                results = list(uploads.map(worker, range(8)))
            self.assertEqual(len({id(pool) for pool, _ in results}), 1)
            self.assertTrue(all(value == 42 for _, value in results))
        finally:
            if hasattr(module, 'crypto_executor'):
                module.crypto_executor.shutdown(wait=True)

    def test_large_upload_initializes_executor_before_client_start(self):
        module = SimpleNamespace()
        class FakeClient:
            def __init__(self, *args, **kwargs): pass
            async def __aenter__(self):
                # Reproduce the original attribute lookup in the transport.
                await asyncio.get_running_loop().run_in_executor(module.crypto_executor, lambda: None)
                return self
            async def __aexit__(self, *args): pass
            async def send_video(self, **kwargs): return SimpleNamespace(id=123)
        try:
            with patch.object(hosts, '_pyrogram', module), patch.object(hosts, 'Client', FakeClient), \
                 patch.object(hosts, 'file_size', return_value=60*1024**2):
                # Exercise the same thread + private event loop path as multi-bot uploads.
                with ThreadPoolExecutor(2) as uploads:
                    results = list(uploads.map(lambda i: hosts._upload_telegram_single(
                        'part.mp4', f'Part {i}', 'token', '-1001234567890', 12345, 'hash', lambda _: None, None), range(2)))
                self.assertEqual(results, ['123', '123'])
        finally:
            if hasattr(module, 'crypto_executor'):
                module.crypto_executor.shutdown(wait=True)


if __name__ == '__main__':
    unittest.main()
