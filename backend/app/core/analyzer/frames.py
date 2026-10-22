"""抽缩略帧。用 ffmpeg 直接输出 JPEG，不经过 Python 图像库。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..ffmpeg import FFMPEG

_NO_WINDOW = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0


def extract_frame(source: str, seconds: float, target: Path,
                  max_width: int = 480, timeout: float = 30.0) -> bool:
    """抓某一时刻的一帧，等比缩到 max_width 宽。失败返回 False。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists() and target.stat().st_size > 512:
        return True
    cmd = [
        FFMPEG, "-hide_banner", "-nostdin", "-y",
        "-ss", f"{max(0.0, seconds):.3f}",
        "-i", source,
        "-frames:v", "1",
        "-vf", f"scale={max_width}:-2",
        "-q:v", "5",
        "-f", "image2", str(target),
    ]
    try:
        subprocess.run(cmd, capture_output=True, timeout=timeout,
                       creationflags=_NO_WINDOW)
    except (subprocess.TimeoutExpired, OSError):
        return False
    return target.exists() and target.stat().st_size > 512
