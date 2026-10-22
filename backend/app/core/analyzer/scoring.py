"""镜头打分。

思路：本地信号（清晰度 / 曝光 / 运动）永远能算，VLM 分是可选的加分项。
没有 VLM 时权重重新归一化，保证"不接 AI 也能用"。
"""

from __future__ import annotations

from typing import List, Optional

# 模糊度 2 接近极限清晰，20 基本糊成一片
_BLUR_CLEAR = 2.0
_BLUR_BLURRY = 20.0

_LOCAL_WEIGHTS = {"sharp": 0.45, "exposure": 0.25, "motion": 0.30}
_VLM_WEIGHTS = {"vlm": 0.35, "sharp": 0.30, "exposure": 0.15, "motion": 0.20}


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, value))


def sharpness_score(blur: Optional[float]) -> float:
    if blur is None:
        # 纯色画面算不出边缘梯度，给中性分，别当成糊片
        return 0.5
    return _clamp(1.0 - (blur - _BLUR_CLEAR) / (_BLUR_BLURRY - _BLUR_CLEAR))


def exposure_score(yavg: Optional[float]) -> float:
    if yavg is None:
        return 0.5
    return _clamp(1.0 - abs(yavg - 128.0) / 128.0 * 1.6)


def motion_score(motion: Optional[float]) -> float:
    if motion is None:
        return 0.5
    if motion < 0.5:
        return 0.2          # 几乎静止，做 hook 很闷
    if motion > 12.0:
        return 0.3          # 剧烈晃动/闪切
    return _clamp(motion / 6.0)


def local_tags(shot: dict) -> List[str]:
    tags: List[str] = []
    blur, yavg, motion = shot.get("blur"), shot.get("yavg"), shot.get("motion")
    if blur is None:
        tags.append("纯色/空镜")
    elif blur > 14:
        tags.append("画面模糊")
    if yavg is not None and yavg < 40:
        tags.append("偏暗")
    elif yavg is not None and yavg > 220:
        tags.append("过曝")
    if motion is not None and motion < 0.6:
        tags.append("静止")
    elif motion is not None and motion > 12:
        tags.append("晃动")
    return tags


def score_shot(shot: dict, vlm: Optional[dict] = None,
               min_hook_score: float = 70.0, min_body_score: float = 40.0) -> dict:
    """返回 {score, level, parts, tags}。vlm 为 None 时只用本地信号。"""
    parts = {
        "sharp": round(sharpness_score(shot.get("blur")), 3),
        "exposure": round(exposure_score(shot.get("yavg")), 3),
        "motion": round(motion_score(shot.get("motion")), 3),
    }
    tags = local_tags(shot)

    if vlm:
        hook = vlm.get("hook_score")
        try:
            parts["vlm"] = round(_clamp(float(hook) / 10.0), 3)
        except (TypeError, ValueError):
            parts["vlm"] = 0.5
        for tag in vlm.get("tags") or []:
            if tag and tag not in tags:
                tags.append(tag)
        if vlm.get("onscreen_text"):
            tags.append("有文字")
        weights = _VLM_WEIGHTS
    else:
        weights = _LOCAL_WEIGHTS

    total = sum(parts[key] * weight for key, weight in weights.items() if key in parts)
    score = round(_clamp(total) * 100, 1)

    # 明确标注不可用的镜头直接压到低分区，选片时会被过滤掉
    if vlm and vlm.get("usage") == "unusable":
        score = min(score, 25.0)

    if score >= min_hook_score:
        level = "hook"
    elif score >= min_body_score:
        level = "body"
    else:
        level = "low"
    return {"score": score, "level": level, "parts": parts, "tags": tags}
