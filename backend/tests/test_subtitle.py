import tempfile
import unittest
from pathlib import Path

from app.core.video_matrix import SharedMediaCache, VideoMatrixCore, build_subtitle_filter
from app.core import subtitle


def config(srt_dir: str, enabled: bool = True) -> dict:
    return {
        "task_name": "subtitle-test",
        "hook_dir": "",
        "body_dirs": [],
        "bgm_dir": "",
        "t_hook": 3.0,
        "t_body": 2.0,
        "total_clips": 3,
        "apply_bgm_to_hook": True,
        "enable_srt": enabled,
        "srt_dir": srt_dir,
    }


class SubtitleTests(unittest.TestCase):
    def test_subtitle_position_uses_top_center_and_bottom_alignment(self):
        self.assertEqual(
            build_subtitle_filter("subtitle.srt", 12),
            "subtitles='subtitle.srt':force_style='FontName=Source Han Sans SC,FontSize=16,Alignment=8,MarginV=35'",
        )
        self.assertEqual(
            build_subtitle_filter("subtitle.srt", 50),
            "subtitles='subtitle.srt':force_style='FontName=Source Han Sans SC,FontSize=16,Alignment=5,MarginV=0'",
        )
        self.assertEqual(
            build_subtitle_filter("subtitle.srt", 88),
            "subtitles='subtitle.srt':force_style='FontName=Source Han Sans SC,FontSize=16,Alignment=2,MarginV=35'",
        )

    def test_subtitle_font_size_uses_video_height_percentage(self):
        subtitle_filter = build_subtitle_filter("subtitle.srt", 88, 7.0, "C\\:/fonts")

        self.assertEqual(
            subtitle_filter,
            "subtitles='subtitle.srt':fontsdir='C\\:/fonts':force_style='FontName=Source Han Sans SC,FontSize=20,Alignment=2,MarginV=35'",
        )

    def test_preflight_rejects_enabled_subtitles_without_directory(self):
        core = VideoMatrixCore(config(""), lambda _message: None, SharedMediaCache())

        ok, message = core.pre_flight_check()

        self.assertFalse(ok)
        self.assertIn("字幕目录不存在或未设置", message)

    def test_preflight_rejects_directory_without_srt(self):
        with tempfile.TemporaryDirectory() as directory:
            core = VideoMatrixCore(config(directory), lambda _message: None, SharedMediaCache())

            ok, message = core.pre_flight_check()

        self.assertFalse(ok)
        self.assertIn("没有找到 SRT 文件", message)

    def test_valid_srt_is_converted_for_ffmpeg(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "sample.srt").write_text(
                "1\n00:00:00,100 --> 00:00:01,200\n测试字幕\n\n",
                encoding="utf-8",
            )
            core = VideoMatrixCore(config(directory), lambda _message: None, SharedMediaCache())

            parsed = core.process_srt(directory, duration_sec=2.0)

            self.assertIsNotNone(parsed)
            self.assertTrue(any(Path(core.temp_dir_path).glob("temp_*.srt")))

    def test_long_subtitle_is_split_into_short_cues(self):
        with tempfile.TemporaryDirectory() as directory:
            Path(directory, "long.srt").write_text(
                "1\n00:00:00,000 --> 00:00:05,000\n"
                "这个白色增压花洒水龙头，出水又细又猛，洗菜不飞溅，厨房卫生间都能用，还特别节水。\n\n",
                encoding="utf-8",
            )
            settings = config(directory)
            settings["subtitle_auto_split"] = True
            settings["subtitle_max_chars"] = 15
            core = VideoMatrixCore(settings, lambda _message: None, SharedMediaCache())

            parsed = core.process_srt(directory, duration_sec=5.0)

            self.assertIsNotNone(parsed)
            written = sorted(Path(core.temp_dir_path).glob("temp_*.srt"))[-1].read_text(encoding="utf-8")
            cues = subtitle.parse_srt(written)
            self.assertGreaterEqual(len(cues), 3)
            for cue in cues:
                self.assertLessEqual(subtitle.char_width(cue["text"]), 15.5)
            self.assertGreaterEqual(cues[-1]["end"], 4800)

    def test_ai_parts_kept_when_text_unchanged(self):
        cues = [{"start": 0, "end": 3000, "text": "这个白色增压花洒水龙头出水又细又猛"}]

        out = subtitle.normalize(cues, 12, 4, [["这个白色增压花洒水龙头", "出水又细又猛"]])

        self.assertEqual([item["text"] for item in out], ["这个白色增压花洒水龙头", "出水又细又猛"])

    def test_ai_parts_rejected_when_text_changes(self):
        cues = [{"start": 0, "end": 3000, "text": "这个白色增压花洒水龙头出水又细又猛"}]

        out = subtitle.normalize(cues, 12, 4, [["这个白色增压花洒", "出水特别猛"]])

        # 改字了就必须回退规则断句，原文不能丢
        self.assertIn("细又猛", "".join(item["text"] for item in out))


if __name__ == "__main__":
    unittest.main()
