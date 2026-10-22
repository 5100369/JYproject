"""随成片导出剪映草稿。

把每条成片实际用到的片段顺序、源入点、时长、原声音量、BGM、配音、字幕
还原成一份剪映草稿，方便在剪映里继续精修。

行为说明（和剪映 11.5 实测一致）
- 写的是明文 `draft_info.json`。剪映打开时会自己升级成加密的
  `draft_content.json` 并补全时间线，所以不要写 draft_content.json。
- 草稿直接落在剪映草稿箱目录，剪映退出重进就能在列表里看到；
  剪映运行期间不刷新列表。
- 只还原"时间线层面"的东西：片段、原声、BGM、配音、字幕。
  FFmpeg 做的去重变换（微裁切/调色/镜像）、随机首帧、水印、B 画面混合
  在剪映里没有等价物，不会出现在草稿中。
"""

from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import List, Optional

US = 1_000_000

_SRT_BLOCK = re.compile(
    r"(\d+)\n(\d{2}:\d{2}:\d{2},\d{3}) --> (\d{2}:\d{2}:\d{2},\d{3})\n(.*?)(?=\n\n|\Z)",
    re.DOTALL,
)


def _srt_ms(value: str) -> int:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.replace(".", ",").split(",")
    return ((int(hours) * 60 + int(minutes)) * 60 + int(seconds)) * 1000 + int(millis)


def read_srt_cues(path: str, limit_seconds: Optional[float] = None,
                  offset_seconds: float = 0.0) -> List[dict]:
    """把 SRT 读成 [{start, duration, text}]，按成片时长裁剪并整体平移。"""
    source = Path(path)
    if not source.is_file():
        return []
    text = None
    for encoding in ("utf-8-sig", "utf-8", "gbk"):
        try:
            text = source.read_text(encoding=encoding)
            break
        except (UnicodeDecodeError, OSError):
            continue
    if text is None:
        return []

    limit_ms = None if limit_seconds is None else int(float(limit_seconds) * 1000)
    offset_ms = int(float(offset_seconds) * 1000)
    cues: List[dict] = []
    for _, start_raw, end_raw, body in _SRT_BLOCK.findall(text + "\n\n"):
        start = _srt_ms(start_raw)
        end = _srt_ms(end_raw)
        if limit_ms is not None and not (end > 0 and start < limit_ms):
            continue
        new_start = max(0, start) + offset_ms
        new_end = (min(end, limit_ms) if limit_ms is not None else end) + offset_ms
        if new_end <= new_start:
            continue
        content = body.strip()
        if not content:
            continue
        cues.append({
            "start": new_start / 1000.0,
            "duration": (new_end - new_start) / 1000.0,
            "text": content,
        })
    return cues

_EMPTY_MATERIAL_KEYS = [
    "ai_translates", "audio_balances", "audio_effects", "audio_tracks",
    "canvases", "chromas", "color_curves", "digital_humans", "drafts",
    "effects", "flowers", "handwrites", "logos", "loudnesses",
    "manual_deformations", "masks", "material_animations",
    "multi_language_refs", "placeholders", "plugin_effects",
    "primary_color_wheels", "realtime_denoises", "shadows",
    "sound_channel_mappings", "speeds", "stabilizations", "stickers",
    "tail_leads", "text_backgrounds", "text_templates", "transitions",
    "video_effects", "video_tracks",
]

_SEGMENT_FLAGS = {
    "enable_adjust": True, "enable_color_correct_adjust": False,
    "enable_color_curves": True, "enable_color_match_adjust": False,
    "enable_color_wheels": True, "enable_lut": True,
    "enable_smart_color_adjust": False, "last_nonzero_volume": 1.0,
    "reverse": False, "track_attribute": 0, "track_render_index": 0, "visible": True,
}

_HDR = {"intensity": 1.0, "mode": 1, "nits": 1000}
_PLATFORM = {"app_id": 3704, "app_source": "lv", "app_version": "5.9.0", "os": "windows"}

DEFAULT_DRAFT_CANDIDATES = (
    ("LOCALAPPDATA", "JianyingPro/User Data/Projects/com.lveditor.draft"),
    ("USERPROFILE", "Videos/JianyingPro/User Data/Projects/com.lveditor.draft"),
)


def _uid() -> str:
    return str(uuid.uuid4()).upper()


def _us(seconds: float) -> int:
    return max(0, int(round(float(seconds) * US)))


def _safe_name(name: str) -> str:
    for ch in '<>:"/\\|?*':
        name = name.replace(ch, "_")
    return name.strip(" .") or "draft"


def _recent_activity(path: Path) -> float:
    """目录里草稿子目录的最新修改时间，用来判断剪映真正在用的是哪一个。"""
    latest = 0.0
    try:
        for entry in path.iterdir():
            if entry.is_dir():
                try:
                    latest = max(latest, entry.stat().st_mtime)
                except OSError:
                    continue
    except OSError:
        return 0.0
    return latest


def resolve_draft_root(configured: str = "") -> Optional[Path]:
    """找剪映草稿箱目录。

    剪映允许把草稿位置改到别的盘，所以不能只认默认路径。存在多个候选时，
    取最近有草稿变动的那一个——剪映只会更新当前正在用的目录。
    """
    if configured:
        path = Path(configured)
        if path.is_dir():
            return path

    candidates: list[Path] = []
    for env, relative in DEFAULT_DRAFT_CANDIDATES:
        base = os.environ.get(env)
        if base:
            candidates.append(Path(base) / relative)
    for drive in ("G", "D", "E", "F", "C"):
        candidates.append(Path(f"{drive}:/jianyinghuancun/JianyingPro Drafts"))

    existing = [path for path in candidates if path.is_dir()]
    if not existing:
        return None
    if len(existing) == 1:
        return existing[0]
    return max(existing, key=_recent_activity)


def _text_content(text: str, size: float) -> str:
    # 缺 styles / 字号等字段时剪映会静默丢掉这段文本
    return json.dumps({
        "styles": [{
            "fill": {"alpha": 1.0, "content": {"render_type": "solid",
                                              "solid": {"alpha": 1.0, "color": [1, 1, 1]}}},
            "range": [0, len(text)],
            "size": float(size),
            "bold": False, "italic": False, "underline": False, "strokes": [],
        }],
        "text": text,
    }, ensure_ascii=False)


def _segment(**overrides) -> dict:
    segment = {
        **_SEGMENT_FLAGS,
        "id": _uid(),
        "common_keyframes": [],
        "keyframe_refs": [],
        "extra_material_refs": [],
    }
    segment.update(overrides)
    return segment


def build_draft_info(
    *,
    name: str,
    width: int,
    height: int,
    fps: float,
    total_duration: float,
    clips: List[dict],
    music_tracks: Optional[List[dict]] = None,
    voice: Optional[dict] = None,
    subtitles: Optional[List[dict]] = None,
    subtitle_y_percent: float = 88.0,
    subtitle_size_percent: float = 5.6,
) -> dict:
    materials = {key: [] for key in _EMPTY_MATERIAL_KEYS}
    videos: list[dict] = []
    audios: list[dict] = []
    texts: list[dict] = []

    video_segments: list[dict] = []
    cursor = 0.0
    for clip in clips:
        source = Path(str(clip["path"]))
        duration = float(clip["duration"])
        src_start = float(clip.get("start", 0.0))
        material_id = _uid()
        videos.append({
            "id": material_id,
            "path": str(source).replace("\\", "/"),
            "duration": _us(src_start + duration + 1.0),
            "height": height,
            "width": width,
            "material_id": "",
            "material_name": source.name,
            "material_type": "video",
            "type": "video",
        })
        video_segments.append(_segment(
            material_id=material_id,
            target_timerange={"duration": _us(duration), "start": _us(cursor)},
            source_timerange={"duration": _us(duration), "start": _us(src_start)},
            clip={
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": 0.0},
            },
            uniform_scale={"on": True, "value": 1.0},
            hdr_settings=dict(_HDR),
            is_tone_modify=False,
            render_index=0,
            volume=max(0.0, min(1.0, float(clip.get("volume", 1.0)))),
            speed=1.0,
        ))
        cursor += duration

    audio_tracks: list[list[dict]] = []
    for track in music_tracks or []:
        duration = float(track.get("duration") or 0)
        if duration <= 0:
            continue
        source = Path(str(track["file"]))
        material_id = _uid()
        audios.append({
            "id": material_id,
            "path": str(source).replace("\\", "/"),
            "duration": _us(max(duration, float(track.get("source_duration") or 0))),
            "material_name": source.name,
            "material_type": "audio",
            "type": "extract_music",
        })
        audio_tracks.append([_segment(
            material_id=material_id,
            target_timerange={"duration": _us(duration), "start": _us(track.get("offset") or 0)},
            source_timerange={"duration": _us(duration), "start": _us(track.get("start") or 0)},
            clip=None,
            is_tone_modify=False,
            render_index=0,
            volume=max(0.0, min(1.0, float(track.get("volume") or 1.0))),
            speed=1.0,
        )])

    if voice and float(voice.get("duration") or 0) > 0:
        source = Path(str(voice["file"]))
        duration = min(float(voice["duration"]), total_duration)
        material_id = _uid()
        audios.append({
            "id": material_id,
            "path": str(source).replace("\\", "/"),
            "duration": _us(duration),
            "material_name": source.name,
            "material_type": "audio",
            "type": "extract_music",
        })
        audio_tracks.append([_segment(
            material_id=material_id,
            target_timerange={"duration": _us(duration), "start": 0},
            source_timerange={"duration": _us(duration), "start": 0},
            clip=None,
            is_tone_modify=False,
            render_index=0,
            volume=max(0.0, min(1.0, float(voice.get("volume") or 1.0))),
            speed=1.0,
        )])

    text_segments: list[dict] = []
    # 剪映 transform.y：0 居中、0.35 约在画面下方；按百分比线性换算
    axis_y = max(-1.0, min(1.0, (float(subtitle_y_percent) - 50.0) / 120.0))
    font_size = max(6.0, float(subtitle_size_percent) * 2.7)
    for subtitle in subtitles or []:
        text = str(subtitle.get("text") or "").strip()
        duration = float(subtitle.get("duration") or 0)
        if not text or duration <= 0:
            continue
        material_id = _uid()
        texts.append({
            "id": material_id,
            "content": _text_content(text, font_size),
            "typesetting": 0,
            "alignment": 0,
            "letter_spacing": 0.0,
            "line_spacing": 0.02,
            "line_feed": 1,
            "line_max_width": 0.82,
            "force_apply_line_max_width": False,
            "check_flag": 7,
            "type": "subtitle",
            "global_alpha": 1.0,
        })
        text_segments.append(_segment(
            material_id=material_id,
            target_timerange={"duration": _us(duration), "start": _us(subtitle.get("start") or 0)},
            # 文本段没有源区间，必须显式给 null
            source_timerange=None,
            clip={
                "alpha": 1.0,
                "flip": {"horizontal": False, "vertical": False},
                "rotation": 0.0,
                "scale": {"x": 1.0, "y": 1.0},
                "transform": {"x": 0.0, "y": axis_y},
            },
            uniform_scale={"on": True, "value": 1.0},
            is_tone_modify=False,
            render_index=1,
            speed=1.0,
            volume=1.0,
        ))

    materials["videos"] = videos
    materials["audios"] = audios
    materials["texts"] = texts

    tracks: list[dict] = []
    if video_segments:
        tracks.append({"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": False,
                       "name": "video", "segments": video_segments, "type": "video"})
    for index, segments in enumerate(audio_tracks):
        tracks.append({"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": False,
                       "name": f"audio{index + 1}", "segments": segments, "type": "audio"})
    if text_segments:
        tracks.append({"attribute": 0, "flag": 0, "id": _uid(), "is_default_name": False,
                       "name": "text", "segments": text_segments, "type": "text"})

    return {
        "canvas_config": {
            "height": height,
            "width": width,
            "ratio": "9:16" if height > width else "16:9",
        },
        "color_space": 0,
        "config": {
            "adjust_max_index": 1,
            "attachment_info": [],
            "combination_max_index": 1,
            "export_range": None,
            "extract_audio_last_index": 1,
            "lyrics_recognition_id": "",
            "lyrics_sync": True,
            "lyrics_taskinfo": [],
            "subtitle_recognition_id": "",
            "video_trackinfo": [],
        },
        "cover": None,
        "create_time": 0,
        "duration": _us(total_duration),
        "extra_info": None,
        "fps": float(fps),
        "free_render_index_mode_on": False,
        "group_container": None,
        "id": _uid(),
        "keyframe_graph_list": [],
        "keyframes": {"adjusts": [], "audios": [], "effects": [], "filters": [],
                      "handwrites": [], "stickers": [], "texts": [], "videos": []},
        "last_modified_platform": dict(_PLATFORM),
        "materials": materials,
        "mutable_config": None,
        "name": name,
        "new_version": "110.0.0",
        "platform": dict(_PLATFORM),
        "relationships": [],
        "render_index_track_mode_on": False,
        "retouch_cover": None,
        "source": "default",
        "state": {},
        "static_cover_image_path": "",
        "time_marks": None,
        "tracks": tracks,
        "update_time": 0,
        "version": 360000,
    }


def build_draft_meta(*, root: Path, folder: str, name: str, total_duration: float) -> dict:
    return {
        "draft_cover": "",
        "draft_fold_path": str(root / folder).replace("\\", "/"),
        "draft_id": _uid(),
        "draft_name": name,
        "draft_removable": True,
        "draft_root_path": str(root).replace("\\", "/"),
        "draft_timeline_materials_size": 0,
        "draft_type": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_last_action_download": "",
        "tm_draft_create": 0,
        "tm_draft_modified": 0,
        "tm_duration": _us(total_duration),
    }


def export_draft(root: Path, name: str, **payload) -> Path:
    """写入一份草稿，返回草稿目录。同名目录已存在时自动加短后缀。"""
    folder = _safe_name(name)
    target = root / folder
    if target.exists():
        folder = f"{folder}_{uuid.uuid4().hex[:6]}"
        target = root / folder
    target.mkdir(parents=True, exist_ok=False)

    info = build_draft_info(name=folder, **payload)
    (target / "draft_info.json").write_text(
        json.dumps(info, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (target / "draft_meta_info.json").write_text(
        json.dumps(build_draft_meta(root=root, folder=folder, name=folder,
                                    total_duration=payload.get("total_duration", 0)),
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return target
