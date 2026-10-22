"""分析结果缓存。

按「绝对路径 + 修改时间 + 文件大小」算 key，素材一改就自动重算，
不会拿旧结论套新素材。结果落在 `%APPDATA%/VideoMatrix/analysis/`。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Optional

# 改了分析逻辑或打分口径就 +1，旧缓存自动失效
STORE_VERSION = 1


def analysis_root() -> Path:
    root = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "VideoMatrix" / "analysis"
    root.mkdir(parents=True, exist_ok=True)
    return root


def cache_key(path: str) -> str:
    stat = os.stat(path)
    payload = f"{os.path.abspath(path).lower()}|{stat.st_mtime_ns}|{stat.st_size}|v{STORE_VERSION}"
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()[:16]


def result_file(path: str) -> Path:
    return analysis_root() / f"{cache_key(path)}.json"


def load(path: str) -> Optional[dict]:
    try:
        target = result_file(path)
    except OSError:
        return None
    if not target.exists():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if data.get("version") != STORE_VERSION:
        return None
    return data


def save(path: str, data: dict) -> str:
    payload = {**data, "version": STORE_VERSION,
               "analyzed_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    target = result_file(path)
    tmp = target.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, target)
    return target.stem


def load_key(key: str) -> Optional[dict]:
    """按缓存 key 取结果（前端拿不到原始路径时用）。"""
    target = analysis_root() / f"{key}.json"
    if not target.is_file():
        return None
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if data.get("version") == STORE_VERSION else None


def thumbs_dir(key: str) -> Path:
    directory = analysis_root() / "thumbs" / key
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def thumb_path(key: str, index: int) -> Path:
    return thumbs_dir(key) / f"{index:03d}.jpg"


def clear_all() -> int:
    """清空全部分析结果，返回删掉的 JSON 数量。"""
    root = analysis_root()
    removed = 0
    for item in root.glob("*.json"):
        try:
            item.unlink()
            removed += 1
        except OSError:
            continue
    thumbs = root / "thumbs"
    if thumbs.is_dir():
        for item in thumbs.iterdir():
            if item.is_dir():
                for child in item.iterdir():
                    try:
                        child.unlink()
                    except OSError:
                        pass
                try:
                    item.rmdir()
                except OSError:
                    pass
    return removed
