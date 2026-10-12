import asyncio
import base64
import hashlib
import json
from pathlib import Path
import random
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.core import framing_preview as preview
from app.models.schemas import FramingPreviewRequest


class FramingPreviewTests(unittest.TestCase):
    def test_real_snapshot_is_small_and_read_only(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            nested = root / '素材'
            nested.mkdir()
            source = nested / 'sample.mp4'
            subprocess.run([preview.FFMPEG, '-nostdin', '-v', 'error', '-y',
                            '-f', 'lavfi', '-i', 'testsrc2=size=720x1280:rate=12',
                            '-t', '1', '-c:v', 'libx264', '-preset', 'ultrafast',
                            '-threads', '1', str(source)], check=True, timeout=20)
            digest = hashlib.sha256(source.read_bytes()).digest()
            state = random.getstate()
            with patch.object(preview, 'run_process', wraps=preview.run_process) as run:
                result = preview.generate_framing_preview([str(root)])
            self.assertEqual(result['file_path'], str(source.resolve()))
            self.assertTrue(0 <= result['time_seconds'] <= .8)
            self.assertEqual(run.call_count, 1)
            command = run.call_args.args[0]
            self.assertIn('-nostdin', command)
            self.assertEqual(command[command.index('-frames:v') + 1], '1')
            self.assertFalse(Path(command[-1]).exists(), 'temporary JPEG must be removed')
            self.assertEqual(hashlib.sha256(source.read_bytes()).digest(), digest)
            self.assertEqual(random.getstate(), state, 'preview must not affect mixing randomness')
            self.assertEqual(list(nested.iterdir()), [source])
            image = root / 'snapshot.jpg'
            data = base64.b64decode(result['image_url'].split(',', 1)[1])
            self.assertTrue(data.startswith(b'\xff\xd8'))
            self.assertLess(len(data), 2 * 1024 * 1024)
            image.write_bytes(data)
            info = json.loads(subprocess.check_output([
                preview.FFPROBE, '-v', 'error', '-show_entries', 'stream=width,height',
                '-of', 'json', str(image)], timeout=5))['streams'][0]
            self.assertEqual((info['width'], info['height']), (540, 960))

    def test_missing_empty_unsupported_and_cancelled_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            for paths in ([], [''], [str(Path(directory) / 'missing')], [directory]):
                with self.subTest(paths=paths), self.assertRaises(ValueError):
                    preview.generate_framing_preview(paths)
            with self.assertRaises(InterruptedError):
                preview.generate_framing_preview([directory], lambda: True)
            # Cancellation/errors always release the singleton decoder slot.
            self.assertTrue(preview._preview_slot.acquire(blocking=False))
            preview._preview_slot.release()

    def test_busy_does_not_start_another_decoder(self):
        preview._preview_slot.acquire()
        try:
            with patch.object(preview, 'run_process') as run, self.assertRaises(preview.PreviewBusyError):
                preview.generate_framing_preview(['unused'])
            run.assert_not_called()
        finally:
            preview._preview_slot.release()

    def test_attached_picture_skipped_and_no_frame_falls_back_once(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mov'
            source.touch()
            metadata = json.dumps(dict(format=dict(duration='2'), streams=[
                dict(index=0, codec_type='video', disposition=dict(attached_pic=1)),
                dict(index=2, codec_type='video', duration='2')]))
            calls = []
            targets = []
            def extract(command, *args, **kwargs):
                calls.append(command)
                targets.append(Path(command[-1]))
                if len(calls) == 2:
                    targets[-1].write_bytes(b'\xff\xd8test')
                return True, ''
            with patch.object(preview.RandomCoverProcessor, '_probe_output', return_value=metadata), \
                    patch.object(preview, 'run_process', side_effect=extract):
                result = preview.generate_framing_preview([str(source)])
            self.assertEqual(result['time_seconds'], 0)
            self.assertEqual(len(calls), 2)
            self.assertEqual(calls[0][calls[0].index('-map') + 1], '0:2')
            self.assertEqual(calls[1][calls[1].index('-ss') + 1], '0.000000000')
            self.assertTrue(all(not target.exists() for target in targets))

    def test_decoder_error_is_clear_and_not_retried(self):
        metadata = json.dumps(dict(streams=[dict(index=0, codec_type='video', duration='1')]))
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / 'source.mp4'
            source.touch()
            with patch.object(preview.RandomCoverProcessor, '_probe_output', return_value=metadata), \
                    patch.object(preview, 'run_process', return_value=(False, 'timeout')) as run:
                with self.assertRaisesRegex(ValueError, '超时'):
                    preview.generate_framing_preview([str(source)])
            self.assertEqual(run.call_count, 1)


class FramingPreviewRouteTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_and_errors(self):
        from app.api.routes import framing_preview
        class Request:
            async def is_disconnected(self):
                return False
        for result, code in (({'image_url': 'data:image/jpeg;base64,ok'}, None),
                             (ValueError('素材无效'), 400), (preview.PreviewBusyError('正在取帧'), 429)):
            with self.subTest(code=code), patch.object(preview, 'generate_framing_preview',
                **({'side_effect': result} if isinstance(result, Exception) else {'return_value': result})):
                if code:
                    with self.assertRaises(HTTPException) as error:
                        await framing_preview(FramingPreviewRequest(paths=['source']), Request())
                    self.assertEqual(error.exception.status_code, code)
                else:
                    self.assertEqual(await framing_preview(FramingPreviewRequest(paths=['source']), Request()), result)

    async def test_disconnect_cancels_worker(self):
        from app.api.routes import framing_preview
        finished = threading.Event()
        class Request:
            async def is_disconnected(self):
                await asyncio.sleep(.02)
                return True
        def worker(paths, cancelled):
            while not cancelled():
                finished.wait(.01)
            finished.set()
            raise InterruptedError('cancelled')
        with patch.object(preview, 'generate_framing_preview', side_effect=worker):
            with self.assertRaises(HTTPException) as error:
                await framing_preview(FramingPreviewRequest(paths=['source']), Request())
            self.assertEqual(error.exception.status_code, 499)
            self.assertTrue(await asyncio.to_thread(finished.wait, 1))
