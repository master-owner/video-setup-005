import contextlib
import io
import json
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import clixarena_hosts as hosts
from clixarena_hosts import merge_provider_mapping, selected_host, selected_hosts, upload_telegram, verify_telegram, HostError
from clixarena_mapping_sync import prune
import clixarena_video as pipeline

class TelegramHostTests(unittest.TestCase):
    identity = {'type': 'movie', 'tmdb_id': 1, 'season': 0, 'episode': 0, 'name': 'Example'}

    def test_telegram_selected_host_and_dual_hosts(self):
        self.assertEqual(selected_host({'uploadHost': 'telegram'}), 'telegram')
        self.assertEqual(selected_hosts({'uploadHost': 'telegram', 'secondUploadHost': 'streamtape'}), ['telegram', 'streamtape'])
        with self.assertRaises(ValueError):
            selected_hosts({'uploadHost': 'telegram', 'secondUploadHost': 'telegram'})

    def test_telegram_mapping_preservation(self):
        rows = merge_provider_mapping([], self.identity, 'stream-id', 'streamtape')
        rows = merge_provider_mapping(rows, self.identity, 'abyss-id', 'abyss')
        rows = merge_provider_mapping(rows, self.identity, 'hg-id', 'streamhg')
        rows = merge_provider_mapping(rows, self.identity, '42', 'telegram')

        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['file_id'], 'stream-id')
        self.assertEqual(rows[0]['abyss_id'], 'abyss-id')
        self.assertEqual(rows[0]['streamhg_id'], 'hg-id')
        self.assertEqual(rows[0]['telegram_id'], '42')
        self.assertEqual(set(rows[0]['providers']), {'streamtape', 'abyss', 'streamhg', 'telegram'})
        self.assertEqual(rows[0]['provider'], 'telegram')

    def test_telegram_multi_part_mapping_preservation(self):
        rows = merge_provider_mapping([], self.identity, '101-102', 'telegram')
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]['telegram_id'], '101-102')
        self.assertEqual(rows[0]['providers']['telegram']['file_id'], '101-102')
        self.assertEqual(rows[0]['providers']['telegram']['parts'], ['101', '102'])

        rows = merge_provider_mapping(rows, self.identity, '101-102-103', 'telegram')
        self.assertEqual(rows[0]['providers']['telegram']['parts'], ['101', '102', '103'])

    def test_telegram_auto_split_2gb_to_4gb_creates_2_parts(self):
        calls = []
        token = '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678'
        chat = '-1001234567890'
        part_counter = [100]

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            # If ffmpeg split command
            if cmd[0] == 'ffmpeg':
                # create the dummy file
                output_file = cmd[-1]
                Path(output_file).write_bytes(b'dummy-split-part')
                return SimpleNamespace(returncode=0, stdout='')
            # If curl upload
            part_counter[0] += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'ok': True,
                'result': {'message_id': part_counter[0], 'video': {'file_size': 1200 * 1024 * 1024}}
            }))

        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=2500 * 1024 * 1024))
        with patch.object(hosts, 'file_size', return_value=2500 * 1024 * 1024), \
             patch.object(hosts, 'get_media_duration', return_value=7200.0):
            res_id = upload_telegram(path, 'Movie.mp4', token, chat, run=fake_run)

        # 2500MB exceeds 2GB -> splits into 2 parts (101, 102)
        self.assertEqual(res_id, '101-102')
        ffmpeg_calls = [c for c in calls if c[0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg_calls), 2)
        self.assertEqual(ffmpeg_calls[0][3], '0.0')
        self.assertEqual(ffmpeg_calls[1][3], '3600.0')

    def test_telegram_auto_split_4gb_to_6gb_creates_3_parts(self):
        calls = []
        token = '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678'
        chat = '-1001234567890'
        part_counter = [200]

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == 'ffmpeg':
                Path(cmd[-1]).write_bytes(b'dummy')
                return SimpleNamespace(returncode=0, stdout='')
            part_counter[0] += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'ok': True,
                'result': {'message_id': part_counter[0]}
            }))

        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=4500 * 1024 * 1024))
        with patch.object(hosts, 'file_size', return_value=4500 * 1024 * 1024), \
             patch.object(hosts, 'get_media_duration', return_value=9000.0):
            res_id = upload_telegram(path, 'Movie.mp4', token, chat, run=fake_run)

        # 4500MB exceeds 4GB -> splits into 3 parts
        self.assertEqual(res_id, '201-202-203')
        ffmpeg_calls = [c for c in calls if c[0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg_calls), 3)

    def test_telegram_auto_split_6gb_to_8gb_creates_4_parts(self):
        calls = []
        token = '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678'
        chat = '-1001234567890'
        part_counter = [300]

        def fake_run(cmd, **kwargs):
            calls.append(cmd)
            if cmd[0] == 'ffmpeg':
                Path(cmd[-1]).write_bytes(b'dummy')
                return SimpleNamespace(returncode=0, stdout='')
            part_counter[0] += 1
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'ok': True,
                'result': {'message_id': part_counter[0]}
            }))

        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=6500 * 1024 * 1024))
        with patch.object(hosts, 'file_size', return_value=6500 * 1024 * 1024), \
             patch.object(hosts, 'get_media_duration', return_value=12000.0):
            res_id = upload_telegram(path, 'Movie.mp4', token, chat, run=fake_run)

        # 6500MB exceeds 6GB -> splits into 4 parts
        self.assertEqual(res_id, '301-302-303-304')
        ffmpeg_calls = [c for c in calls if c[0] == 'ffmpeg']
        self.assertEqual(len(ffmpeg_calls), 4)

    def test_telegram_invalid_credentials_rejected(self):
        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=1024))
        with patch.object(hosts, 'file_size', return_value=1024):
            with self.assertRaises(HostError):
                upload_telegram(path, 'movie.mp4', 'invalid-token', '-1001234567890')
            with self.assertRaises(HostError):
                upload_telegram(path, 'movie.mp4', '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678', 'not-a-chat-id')

    def test_telegram_upload_masks_secrets_and_parses_message_id(self):
        calls = []
        token = '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678'
        chat = '-1001234567890'
        masked = []

        def run(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=0, stdout=json.dumps({
                'ok': True,
                'result': {'message_id': 999, 'video': {'file_size': 1000}}
            }))

        with patch.object(hosts, 'file_size', return_value=1000):
            msg_id = upload_telegram(Path('clixarena.mp4'), 'Title.mp4', token, chat, mask=lambda v: masked.append(v), run=run)

        self.assertEqual(msg_id, '999')
        self.assertEqual(len(calls), 1)
        self.assertNotIn(token, str(calls[0][0]))
        self.assertIn(token, masked)
        self.assertIn(chat, masked)
        self.assertIn(f'chat_id={chat}', calls[0][1]['input'])

    def test_telegram_completion_pipeline(self):
        calls = []
        output = io.StringIO()
        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=1024))
        env = {
            'OVERLAY_LAYOUT': '{"uploadHost":"telegram"}',
            'GITHUB_OUTPUT': '',
            'TELEGRAM_BOT_TOKEN': '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678',
            'TELEGRAM_CHAT_ID': '-1001234567890'
        }

        with patch.dict(os.environ, env), \
             patch.object(pipeline, 'upload_telegram_multi', lambda *args, **kwargs: ('42', {'1080p': '42'})), \
             patch.object(pipeline, 'verify_telegram', lambda *args: (True, True)), \
             patch.object(pipeline, 'save_mapping', lambda *args: calls.append(args)), \
             patch.object(pipeline, 'upload', side_effect=AssertionError('Wrong host')), \
             contextlib.redirect_stdout(output):
            pipeline.complete_upload(path, 'Title.mp4', env)

        self.assertEqual(calls, [('42', 'telegram', {'1080p': '42'})])
        self.assertNotIn('123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678', output.getvalue())

    def test_verify_telegram(self):
        self.assertEqual(verify_telegram('123', 'token', 100, 'Title.mp4'), (True, True))
        self.assertEqual(verify_telegram(456, 'token', 100, 'Title.mp4'), (True, True))
        self.assertEqual(verify_telegram('101-102', 'token', 100, 'Title.mp4'), (True, True))
        self.assertEqual(verify_telegram('101-102-103', 'token', 100, 'Title.mp4'), (True, True))
        self.assertEqual(verify_telegram('not-a-number', 'token', 100, 'Title.mp4'), (False, False))


    def test_upload_telegram_multi_resolution_1080p_creates_three_resolutions(self):
        uploaded = []
        def fake_upload(p, name, token, chat, *args, **kwargs):
            uploaded.append(name)
            return 'msg-' + name.split('[')[1].split(']')[0]

        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=1024))
        with patch.object(hosts, 'get_video_dimensions', return_value=(1920, 1080)), \
             patch.object(hosts, 'transcode_resolution', lambda *a, **k: None), \
             patch.object(hosts, 'upload_telegram', side_effect=fake_upload):
            primary, q_ids = hosts.upload_telegram_multi(path, 'Movie.mp4', '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678', '-1001234567890')

        self.assertEqual(primary, 'msg-1080p')
        self.assertEqual(q_ids, {'1080p': 'msg-1080p', '720p': 'msg-720p', '480p': 'msg-480p'})
        self.assertEqual(uploaded, ['Movie [1080p].mp4', 'Movie [720p].mp4', 'Movie [480p].mp4'])

    def test_upload_telegram_multi_resolution_720p_creates_two_resolutions(self):
        uploaded = []
        def fake_upload(p, name, token, chat, *args, **kwargs):
            uploaded.append(name)
            return 'msg-' + name.split('[')[1].split(']')[0]

        path = SimpleNamespace(stat=lambda: SimpleNamespace(st_size=1024))
        with patch.object(hosts, 'get_video_dimensions', return_value=(1280, 720)), \
             patch.object(hosts, 'transcode_resolution', lambda *a, **k: None), \
             patch.object(hosts, 'upload_telegram', side_effect=fake_upload):
            primary, q_ids = hosts.upload_telegram_multi(path, 'Movie.mp4', '123456789:ABCdefGHIjklMNOpqrsTUVwxyz12345678', '-1001234567890')

        self.assertEqual(primary, 'msg-720p')
        self.assertEqual(q_ids, {'720p': 'msg-720p', '480p': 'msg-480p'})
        self.assertEqual(uploaded, ['Movie [720p].mp4', 'Movie [480p].mp4'])

if __name__ == '__main__':
    unittest.main()
