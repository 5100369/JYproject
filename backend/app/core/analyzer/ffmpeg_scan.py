"""用 ffmpeg 自带滤镜做画面体检，不引入 numpy / OpenCV。

一条命令同时拿到四类信号（fps 采样）：

- `lavfi.scd.score`  场景切换强度（超过阈值就是切镜点）
- `lavfi.scd.mafd`   当前帧与上一帧的平均绝对差 —— 运动强度
- `lavfi.blur`       模糊度，**数值越小越清晰**（testsrc 约 4.6，糊片会到 15+）
- `lavfi.signalstats.YAVG` 亮度均值，判断欠曝/过曝

这样整个分析层零额外依赖，打包体积不受影响。
"""

from __future__ import annotations

import os
import re
import subprocess
from typing import List, Optional

from ..ffmpeg import FFMPEG, extract_media_info, probe_media

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0

_FRAME_RE = re.compile(r"^frame:\s*\d+.*?pts_time:\s*(-?[0-9.]+)")
_METRIC_RE = re.compile(r"^lavfi\.([a-zA-Z0-9.]+)=(-?[0-9.]+)")

_KEEP = {"scd.score", "scd.mafd", "blur", "signalstats.YAVG"}

# 相邻切镜点近于这个秒数时合并，避免闪白/闪黑被切成碎镜头
_MIN_SHOT_SECONDS = 0.8


def probe_video(path: str) -> Optional[dict]:
    info = probe_media(path)
    if not info:
        return None
    duration, has_audio, width, height, fps = extract_media_info(info, path)
    if not duration or duration <= 0:
        return None
    return {
        "duration": float(duration),
        "width": int(width or 0),
        "height": int(height or 0),
        "fps": float(fps or 0),
        "has_audio": bool(has_audio),
    }


def parse_metrics(text: str) -> List[dict]:
    """把 `metadata=print` 的文本输出解析成逐帧样本。"""
    samples: List[dict] = []
    current_time: Optional[float] = None
    current: dict = {}

    def flush():
        if current_time is None:
            return
        samples.append({
            "t": current_time,
            "cut": current.get("scd.score", 0.0),
            "motion": current.get("scd.mafd", 0.0),
            "blur": current.get("blur"),
            "yavg": current.get("signalstats.YAVG"),
        })

    for raw in text.splitlines():
        line = raw.strip()
        frame_match = _FRAME_RE.match(line)
        if frame_match:
            flush()
            current_time = float(frame_match.group(1))
            current = {}
            continue
        metric_match = _METRIC_RE.match(line)
        if metric_match and current_time is not None:
            key, value = metric_match.group(1), float(metric_match.group(2))
            if key in _KEEP:
                current[key] = value
    flush()
    return samples


def scan(path: str, sample_fps: float = 2.0, scene_threshold: float = 10.0,
         timeout: float = 900.0) -> List[dict]:
    filters = (
        f"fps={sample_fps},"
        f"scdet=threshold={scene_threshold},"
        "blurdetect=block_width=32:block_height=32,"
        "signalstats,metadata=print:file=-"
    )
    cmd = [FFMPEG, "-hide_banner", "-nostdin", "-i", path, "-an",
           "-vf", filters, "-f", "null", "-"]
    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, creationflags=_NO_WINDOW,
    )
    # metadata 走 stdout；ffmpeg 的进度信息在 stderr，不用管
    return parse_metrics(proc.stdout or "")


def build_shots(samples: List[dict], duration: float,
                threshold: float = 10.0) -> List[dict]:
    """按切镜点把视频分成镜头，并把逐帧指标聚合到每个镜头。"""
    if not samples or duration <= 0:
        return []

    cuts = sorted({round(s["t"], 2) for s in samples if s["cut"] >= threshold and s["t"] > 0.05})
    bounds: List[float] = [0.0]
    for cut in cuts:
        if cut - bounds[-1] >= _MIN_SHOT_SECONDS:
            bounds.append(cut)
    if duration - bounds[-1] < _MIN_SHOT_SECONDS and len(bounds) > 1:
        bounds.pop()
    bounds.append(float(duration))

    shots: List[dict] = []
    for index in range(len(bounds) - 1):
        start, end = bounds[index], bounds[index + 1]
        inside = [s for s in samples if start - 1e-6 <= s["t"] < end - 1e-6]
        if not inside:
            inside = [s for s in samples if abs(s["t"] - start) < 0.5] or [samples[0]]

        def average(key: str) -> Optional[float]:
            values = [s[key] for s in inside if s.get(key) is not None]
            return round(sum(values) / len(values), 3) if values else None

        shots.append({
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "blur": average("blur"),
            "yavg": average("yavg"),
            "motion": average("motion"),
            "cut": round(max((s["cut"] for s in inside), default=0.0), 2),
        })
    return shots
