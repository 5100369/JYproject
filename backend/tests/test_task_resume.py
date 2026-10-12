import copy
import hashlib
from datetime import datetime
from pathlib import Path
import tempfile
import subprocess
import threading
import unittest
from unittest.mock import patch

from fastapi import HTTPException
from app.core.video_matrix import SharedMediaCache
from app.models.schemas import TaskStatus, VideoConfig
from app.services.task_service import TaskService, TaskBusyError
from app.core.ffmpeg import FFMPEG, FFPROBE
import json


class TaskResumeTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.service = TaskService(SharedMediaCache(str(self.root / 'state')))
        self.config = VideoConfig(hook_dir='hook', base_out_dir=str(self.root / 'output'),
                                  target_count=3, concurrent_tasks=1, hook_zoom_percent=150,
                                  random_resolution_enabled=True, enable_random_cover=True)
        self.specs = [dict(name='A', hook_dir='A', body_dirs=['A-body'], out=str(self.root / 'A'))]
        self.calls, self.created, self.sessions = [], [], []
        self.render = lambda core, index: True
        outer = self

        class Core:
            def __init__(core, config, log, shared):
                core.config, core.log, core.shared = config, log, shared
                core.task_name, core.is_running = config['task_name'], True
                core.output_configs = {}
                outer.created.append(copy.copy(config))

            def pre_flight_check(core):
                return True, 'ok'

            def stop(core):
                core.is_running = False

            def render_single_video(core, index, return_result=False):
                outer.calls.append((core.task_name, index))
                if not outer.render(core, index):
                    return False, None, None
                output = Path(core.config['out_dir']) / f'{core.task_name}-{index}.mp4'
                # An accidental rerender of a completed slot is a test failure.
                if output.exists():
                    raise AssertionError('completed output must never be rerendered')
                output.write_bytes(f'{core.task_name}:{index}'.encode())
                core.output_configs[index] = {**core.config, '_cover_applied': True}
                core.shared.usage_history.add(f'{core.task_name}-{index}')
                core.shared.save_state()
                return True, str(output), 1.0

        class Session:
            def __init__(session, *args, **kwargs):
                session.closed = False
                outer.sessions.append(session)
            def close(session):
                session.closed = True

        for target, options in [
            (('app.services.task_service.VideoMatrixCore',), dict(side_effect=Core)),
            (('app.services.task_service.HardwareSession',), dict(side_effect=Session)),
        ]:
            patcher = patch(*target, **options)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.start = patch.object(self.service, '_start_production_run')
        self.launch = self.start.start()
        self.addCleanup(self.start.stop)
        self.catalog = patch.object(self.service, '_get_tasks_from_config', side_effect=lambda cfg: copy.deepcopy(self.specs))
        self.catalog_mock = self.catalog.start()
        self.addCleanup(self.catalog.stop)

    def run_pipeline(self, task_id):
        self.service._run_pipeline(task_id, copy.deepcopy(self.service._production_runs[task_id].config))

    def test_stop_continue_keeps_id_settings_files_logs_and_remaining_indices(self):
        task_id = self.service.create_task(self.config)
        def stop_at_two(core, index):
            if index == 2:
                self.service.stop_task(task_id)
                return False
            return True
        self.render = stop_at_two
        self.run_pipeline(task_id)
        status = self.service.get_task(task_id)
        self.assertEqual((status.status, status.current, status.total, status.resume_remaining), ('stopped', 1, 3, 2))
        self.assertTrue(status.can_resume)
        first = Path(status.output_files[0])
        original = first.read_bytes()
        logs = list(status.log_lines)
        frozen = copy.deepcopy(self.service._production_runs[task_id].config)
        self.config.hook_zoom_percent = 250  # Later UI edits must not alter this task.
        self.config.target_count = 9
        self.render = lambda core, index: True
        self.assertTrue(self.service.resume_task(task_id))
        with self.assertRaises(TaskBusyError):
            self.service.resume_task(task_id)
        self.assertEqual(status.current, 1)
        self.assertEqual(status.output_files, [str(first)])
        self.run_pipeline(task_id)
        self.assertEqual((status.status, status.current, status.total), ('completed', 3, 3))
        self.assertFalse(status.can_resume)
        self.assertEqual(self.calls, [('A', 1), ('A', 2), ('A', 2), ('A', 3)])
        self.assertEqual(first.read_bytes(), original)
        self.assertEqual(status.log_lines[:len(logs)], logs)
        self.assertEqual(self.catalog_mock.call_count, 1, 'resume must not discover new SKU folders')
        self.assertEqual(len(self.sessions), 2)
        self.assertTrue(all(s.closed for s in self.sessions))
        self.assertNotIn(task_id, self.service._production_runs, 'completed checkpoints should release memory')
        for key in ('hook_zoom_percent', '_cover_seed', '_output_seed', 'resolution', 'bitrate'):
            self.assertEqual(self.created[-1][key], frozen[key])
        self.assertEqual(self.service.shared_cache.usage_history, {'A-1', 'A-2', 'A-3'})

    def test_multi_sku_continues_only_holes_and_does_not_probe_completed_sku(self):
        self.config.target_count = 2
        self.specs.append(dict(name='B', hook_dir='B', body_dirs=['B-body'], out=str(self.root / 'B')))
        task_id = self.service.create_task(self.config)
        def stop_at_b_two(core, index):
            if (core.task_name, index) == ('B', 2):
                self.service.stop_task(task_id)
                return False
            return True
        self.render = stop_at_b_two
        self.run_pipeline(task_id)
        self.assertEqual(self.service.get_task(task_id).resume_remaining, 1)
        self.render = lambda core, index: True
        self.service.resume_task(task_id)
        self.run_pipeline(task_id)
        self.assertEqual([cfg['task_name'] for cfg in self.created], ['A', 'B', 'B'])
        self.assertEqual(self.calls, [('A', 1), ('B', 1), ('A', 2), ('B', 2), ('B', 2)])
        self.assertEqual((self.service.get_task(task_id).total, len(self.service.get_task(task_id).output_files)), (4, 4))

    def test_failed_job_can_retry_without_redoing_usable_warning_output(self):
        self.config.target_count = 2
        task_id = self.service.create_task(self.config)
        self.render = lambda core, index: index == 1
        self.run_pipeline(task_id)
        status = self.service.get_task(task_id)
        self.assertEqual(status.status, 'partial')
        status.output_warnings[status.output_files[0]] = '已保留基础视频'
        self.assertTrue(status.can_resume)
        self.render = lambda core, index: True
        self.service.resume_task(task_id)
        self.run_pipeline(task_id)
        self.assertEqual(status.status, 'partial')  # Keep the old warning, don't silently claim perfection.
        self.assertFalse(status.can_resume)
        self.assertEqual(len(status.output_files), 2)
        self.assertEqual(self.calls, [('A', 1), ('A', 2), ('A', 2)])

    def test_stop_before_start_can_continue_and_benchmark_blocks_resume(self):
        task_id = self.service.create_task(self.config)
        self.service.stop_task(task_id)
        self.assertTrue(self.service.get_task(task_id).stopping)
        with self.assertRaises(TaskBusyError):
            self.service.resume_task(task_id)
        self.run_pipeline(task_id)
        self.assertTrue(self.service.get_task(task_id).can_resume)
        self.service._benchmark_task_id = 'benchmark-test'
        with self.assertRaises(TaskBusyError):
            self.service.resume_task(task_id)
        self.service._benchmark_task_id = None
        self.service.resume_task(task_id)
        self.run_pipeline(task_id)
        self.assertEqual(self.service.get_task(task_id).status, 'completed')

    def test_stop_in_preflight_does_not_lose_later_sku_libraries(self):
        self.config.target_count = 1
        self.specs.append(dict(name='B', hook_dir='B', body_dirs=['B'], out=str(self.root / 'B')))
        task_id = self.service.create_task(self.config)
        def preflight(core):
            self.service.stop_task(task_id)
            self.assertFalse(core.is_running, 'preflight core must already be registered for stop')
            return False, '已停止'
        with patch('app.services.task_service.VideoMatrixCore') as factory:
            class PreparingCore:
                is_running = True
                def stop(core): core.is_running = False
                def pre_flight_check(core): return preflight(core)
            factory.return_value = PreparingCore()
            self.run_pipeline(task_id)
        self.assertIsNone(self.service._production_runs[task_id].jobs)
        self.assertTrue(self.service.get_task(task_id).can_resume)
        self.service.resume_task(task_id)
        self.run_pipeline(task_id)
        self.assertEqual(self.calls, [('A', 1), ('B', 1)])
        self.assertEqual(self.service.get_task(task_id).total, 2)

    def test_late_finished_output_is_credited_only_after_workers_drain(self):
        self.config.target_count = 1
        task_id = self.service.create_task(self.config)
        entered, release = threading.Event(), threading.Event()
        def late(core, index):
            entered.set()
            if not release.wait(3):
                raise AssertionError('worker was not released')
            return True
        self.render = late
        worker = threading.Thread(target=self.run_pipeline, args=(task_id,))
        worker.start()
        try:
            self.assertTrue(entered.wait(2))
            self.service.stop_task(task_id)
            self.assertTrue(self.service.get_task(task_id).stopping)
            self.assertFalse(self.service.get_task(task_id).can_resume)
            with self.assertRaises(TaskBusyError):
                self.service.resume_task(task_id)
            with self.assertRaises(TaskBusyError):
                self.service.get_benchmark(self.config)
        finally:
            release.set()
            worker.join(4)
        self.assertFalse(worker.is_alive())
        status = self.service.get_task(task_id)
        self.assertEqual((status.current, status.total, len(status.output_files)), (1, 1, 1))
        self.assertFalse(status.stopping)
        self.assertFalse(status.can_resume)
        with self.assertRaises(ValueError):
            self.service.resume_task(task_id)

    def test_unknown_benchmark_and_completed_task_rejections(self):
        self.assertFalse(self.service.resume_task('missing'))
        self.service.tasks['benchmark-test'] = TaskStatus(task_id='benchmark-test', task_name='benchmark', status='stopped', created_at=datetime.now())
        with self.assertRaises(ValueError):
            self.service.resume_task('benchmark-test')
        task_id = self.service.create_task(self.config)
        self.run_pipeline(task_id)
        with self.assertRaises(ValueError):
            self.service.resume_task(task_id)

    def test_worker_launch_failure_remains_retryable(self):
        task_id = self.service.create_task(self.config)
        with patch('app.services.task_service.threading.Thread') as thread:
            thread.return_value.start.side_effect = RuntimeError('thread unavailable')
            with self.assertRaises(RuntimeError):
                TaskService._start_production_run(self.service, task_id)
        self.assertTrue(self.service.get_task(task_id).can_resume)
        self.service.resume_task(task_id)
        self.run_pipeline(task_id)
        self.assertEqual(self.service.get_task(task_id).status, 'completed')

    def test_resume_api_maps_busy_invalid_and_missing_and_returns_same_id(self):
        from app.api.routes import resume_task
        for result, code in ((True, None), (False, 404), (ValueError('不可继续'), 400), (TaskBusyError('清理中'), 409)):
            with self.subTest(code=code), patch('app.api.routes.task_service.resume_task',
                **({'side_effect': result} if isinstance(result, Exception) else {'return_value': result})):
                if code:
                    with self.assertRaises(HTTPException) as error:
                        resume_task('original-id')
                    self.assertEqual(error.exception.status_code, code)
                else:
                    self.assertEqual(resume_task('original-id').task_id, 'original-id')


class RealTaskResumeTests(unittest.TestCase):
    def test_real_encode_stop_resume_keeps_finished_video_and_hook_history(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            hooks, bodies, out = root / 'hooks', root / 'bodies', root / 'output'
            hooks.mkdir(); bodies.mkdir()
            for folder, duration in ((hooks, '4'), (bodies, '1.5')):
                subprocess.run([FFMPEG, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi',
                                '-i', 'testsrc2=size=160x284:rate=12', '-t', duration,
                                '-c:v', 'libx264', '-preset', 'ultrafast', '-threads', '1',
                                str(folder / 'source.mp4')], check=True, timeout=15)
            cache = SharedMediaCache(str(root / 'state'))
            service = TaskService(cache)
            config = VideoConfig(hook_dir=str(hooks), body_dirs=[str(bodies)], base_out_dir=str(out),
                                 t_hook=.5, t_body=.5, total_clips=2, target_count=3, concurrent_tasks=1,
                                 hook_r=0, resolution='160*284', fps='12', bitrate='500k', enable_gpu=False)
            with patch.object(service, '_start_production_run'):
                task_id = service.create_task(config)
                register = service._register_output
                def stop_after_output(*args, **kwargs):
                    result = register(*args, **kwargs)
                    service.stop_task(task_id)
                    return result
                with patch.object(service, '_register_output', side_effect=stop_after_output):
                    service._run_pipeline(task_id, copy.deepcopy(service._production_runs[task_id].config))
                status = service.get_task(task_id)
                self.assertEqual((len(status.output_files), status.resume_remaining), (1, 2))
                first = Path(status.output_files[0])
                first_hash = hashlib.sha256(first.read_bytes()).digest()
                self.assertTrue(service.resume_task(task_id))
                service._run_pipeline(task_id, copy.deepcopy(service._production_runs[task_id].config))
            self.assertEqual(status.status, 'completed', '\n'.join(status.log_lines))
            self.assertEqual((status.current, status.total, len(status.output_files)), (3, 3, 3))
            self.assertEqual(hashlib.sha256(first.read_bytes()).digest(), first_hash)
            self.assertEqual(len(cache.usage_history), 3)
            self.assertEqual(len(list(out.rglob('*.mp4'))), 3)
            for file in status.output_files:
                info = json.loads(subprocess.check_output([FFPROBE, '-v', 'error', '-show_entries',
                    'format=duration:stream=codec_type,width,height', '-of', 'json', file], timeout=5))
                self.assertEqual((info['streams'][0]['width'], info['streams'][0]['height']), (160, 284))
                self.assertAlmostEqual(float(info['format']['duration']), 1, delta=.09)
