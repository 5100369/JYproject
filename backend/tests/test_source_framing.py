import unittest

from pydantic import ValidationError
from app.models.schemas import VideoConfig
from app.core.source_framing import source_crop_filter, source_zoom
from app.core.timeline import apply_timeline_totals


class SourceFramingTests(unittest.TestCase):
    def test_defaults_are_an_exact_bypass_and_scopes_are_independent(self):
        self.assertEqual(source_crop_filter({}, True), '')
        self.assertEqual(source_crop_filter({}, False), '')
        config = dict(hook_zoom_percent=200, body_zoom_percent=150)
        self.assertIn('iw/2.000000000', source_crop_filter(config, True))
        self.assertIn('iw/1.500000000', source_crop_filter(config, False))

    def test_schema_and_direct_call_validate_both_scopes(self):
        for key in ('hook_zoom_percent', 'body_zoom_percent'):
            for bad in (99, 301, 'bad', float('nan'), float('inf')):
                with self.subTest(key=key, bad=bad):
                    with self.assertRaises(ValidationError):
                        VideoConfig(hook_dir='hook', **{key: bad})
                    with self.assertRaises(ValueError):
                        source_zoom({key: bad}, key.startswith('hook'))
        config = VideoConfig(hook_dir='hook')
        self.assertEqual((config.hook_zoom_percent, config.body_zoom_percent), (100, 100))

    def test_ten_groups_are_accepted_and_eleventh_is_rejected(self):
        groups = [dict(enabled=True, folder=f'group-{i}', clip_count=1, clip_duration=.5)
                  for i in range(10)]
        config = VideoConfig(hook_dir='hook', t_hook=.5, body_mode='grouped', body_groups=groups)
        resolved = apply_timeline_totals(config.model_dump())
        self.assertEqual(resolved['total_clips'], 11)
        self.assertEqual(resolved['total_duration'], 5.5)
        with self.assertRaises(ValidationError):
            VideoConfig(hook_dir='hook', body_groups=groups + [groups[0]])
