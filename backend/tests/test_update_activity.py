from datetime import datetime
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.core.video_matrix import SharedMediaCache
from app.models.schemas import TaskStatus
from app.services.task_service import TaskService, _ProductionRun


class UpdateActivityTests(unittest.TestCase):
    def setUp(self):
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.service = TaskService(SharedMediaCache(str(Path(self.folder.name) / 'state')))

    def task(self, status='running'):
        self.service.tasks['a'] = TaskStatus(task_id='a', task_name='A', status=status, created_at=datetime.now(), log_lines=['private logs'])

    def test_idle_and_pending_do_not_copy_logs(self):
        self.assertEqual(self.service.get_activity(), {'busy': False, 'resumable': 0})
        self.task('pending')
        self.assertEqual(self.service.get_activity(), {'busy': True, 'resumable': 0})
        self.assertNotIn('log_lines', self.service.get_activity())

    def test_cleanup_and_benchmark_remain_busy_after_visible_stop(self):
        self.task('stopped')
        run = _ProductionRun({}, active=True)
        self.service._production_runs['a'] = run
        self.assertTrue(self.service.get_activity()['busy'])
        run.active = False
        self.assertFalse(self.service.get_activity()['busy'])
        self.service._benchmark_task_id = 'benchmark-a'
        self.assertTrue(self.service.get_activity()['busy'])
        self.service._benchmark_task_id = None
        self.service.variant_processes['a'] = {object()}
        self.assertTrue(self.service.get_activity()['busy'])

    def test_completed_slots_and_resumable_counts(self):
        self.task('stopped')
        run = _ProductionRun({}, jobs=[(0, 0), (0, 1)], active=False)
        run.outputs[(0, 0)] = 'existing.mp4'
        self.service._production_runs['a'] = run
        self.assertEqual(self.service.get_activity(), {'busy': False, 'resumable': 1})
        run.outputs[(0, 1)] = 'complete.mp4'
        self.assertEqual(self.service.get_activity()['resumable'], 0)

    def test_activity_route_precedes_dynamic_task_id(self):
        from starlette.routing import Match
        from app.api import routes
        scope = {'type': 'http', 'method': 'GET', 'path': '/tasks/activity', 'root_path': ''}
        matching = [route for route in routes.router.routes if route.matches(scope)[0] == Match.FULL]
        self.assertIs(matching[0].endpoint, routes.task_activity)
        with patch.object(routes, 'task_service', self.service):
            self.assertEqual(routes.task_activity(), {'busy': False, 'resumable': 0})


if __name__ == '__main__':
    unittest.main()
