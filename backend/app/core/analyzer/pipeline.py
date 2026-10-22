"""单条素材的分析编排：切镜 → 抽帧 → （可选）视觉理解 → 打分 → 落盘。

设计原则：**本地信号永远算，VLM 可选**。没配 Key 或调用失败时，
自动降级成纯本地打分，结果照样能用。
"""

from __future__ import annotations

import os
import concurrent.futures
from typing import Callable, List, Optional

from . import store
from .ffmpeg_scan import build_shots, probe_video, scan
from .frames import extract_frame
from .scoring import score_shot


def _sample_evenly(items: List[dict], limit: int) -> List[dict]:
    if limit <= 0 or len(items) <= limit:
        return items
    step = len(items) / float(limit)
    return [items[int(i * step)] for i in range(limit)]


def analyze_file(
    path: str,
    *,
    use_vlm: bool = False,
    max_shots: int = 60,
    sample_fps: float = 2.0,
    scene_threshold: float = 10.0,
    progress: Optional[Callable[[str, int, int], None]] = None,
    cancel=None,
) -> dict:
    def report(stage: str, done: int, total: int) -> None:
        if progress:
            progress(stage, done, total)

    def stopped() -> bool:
        return cancel is not None and cancel.is_set()

    info = probe_video(path)
    if not info:
        raise ValueError(f"无法读取视频信息：{os.path.basename(path)}")

    key = store.cache_key(path)

    report("scan", 0, 1)
    samples = scan(path, sample_fps=sample_fps, scene_threshold=scene_threshold)
    if stopped():
        raise InterruptedError("已取消")

    shots = build_shots(samples, info["duration"], scene_threshold)
    if not shots:
        shots = [{
            "index": 0, "start": 0.0, "end": info["duration"],
            "duration": info["duration"], "blur": None, "yavg": None,
            "motion": None, "cut": 0.0,
        }]

    # 镜头太长（如整条没切镜）时按固定时长再切一刀，否则选片粒度太粗
    shots = _split_long_shots(shots, info["duration"], max_len=8.0)
    shots = _sample_evenly(shots, max_shots)
    for order, shot in enumerate(shots):
        shot["index"] = order
    report("scan", 1, 1)

    # 抽缩略图
    thumbs: List[str] = []
    thumb_dir = store.thumbs_dir(key)

    def grab(shot: dict) -> Optional[str]:
        if stopped():
            return None
        target = store.thumb_path(key, shot["index"])
        center = shot["start"] + min(shot["duration"] / 2.0, 0.9)
        ok = extract_frame(path, center, target)
        return target.name if ok else None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        for done, name in enumerate(executor.map(grab, shots), 1):
            thumbs.append(name or "")
            report("thumb", done, len(shots))
    if stopped():
        raise InterruptedError("已取消")

    # 视觉理解（可选）
    vlm_results: dict = {}
    vlm_errors = 0
    if use_vlm:
        from .vision import analyze_frame
        targets = [(shot["index"], name) for shot, name in zip(shots, thumbs) if name]
        if targets:
            def look(item):
                index, name = item
                return index, analyze_frame(str(thumb_dir / name))

            with concurrent.futures.ThreadPoolExecutor(max_workers=3) as executor:
                for done, (index, result) in enumerate(executor.map(look, targets), 1):
                    if result:
                        vlm_results[index] = result
                    else:
                        vlm_errors += 1
                    report("vlm", done, len(targets))
    if stopped():
        raise InterruptedError("已取消")

    # 打分
    scored: List[dict] = []
    for shot, name in zip(shots, thumbs):
        vlm = vlm_results.get(shot["index"])
        verdict = score_shot(shot, vlm)
        scored.append({
            **shot,
            "thumb": name,
            "score": verdict["score"],
            "level": verdict["level"],
            "parts": verdict["parts"],
            "tags": verdict["tags"],
            "vlm": vlm,
        })

    scored.sort(key=lambda item: item["start"])
    best = max(scored, key=lambda item: item["score"]) if scored else None
    result = {
        "file": path,
        "name": os.path.basename(path),
        "duration": round(info["duration"], 3),
        "width": info["width"],
        "height": info["height"],
        "fps": info["fps"],
        "has_audio": info["has_audio"],
        "vlm_used": bool(vlm_results),
        "vlm_errors": vlm_errors,
        "shots": scored,
        "summary": {
            "shot_count": len(scored),
            "avg_score": round(sum(s["score"] for s in scored) / len(scored), 1) if scored else 0,
            "hook_count": sum(1 for s in scored if s["level"] == "hook"),
            "body_count": sum(1 for s in scored if s["level"] == "body"),
            "low_count": sum(1 for s in scored if s["level"] == "low"),
            "best_index": best["index"] if best else None,
            "best_score": best["score"] if best else 0,
        },
    }
    store.save(path, result)
    report("done", 1, 1)
    return result


def _split_long_shots(shots: List[dict], duration: float, max_len: float = 8.0) -> List[dict]:
    """没有切镜的长镜头按 max_len 继续切分，避免"整条当一个镜头"。"""
    out: List[dict] = []
    for shot in shots:
        length = shot["duration"]
        if length <= max_len * 1.5:
            out.append(shot)
            continue
        pieces = max(2, int(round(length / max_len)))
        step = length / pieces
        for i in range(pieces):
            out.append({
                **shot,
                "start": round(shot["start"] + i * step, 3),
                "end": round(min(duration, shot["start"] + (i + 1) * step), 3),
                "duration": round(step, 3),
            })
    for order, shot in enumerate(out):
        shot["index"] = order
    return out
