"""On-demand, read-only source snapshots; never enter the production pipeline."""
import base64
import json
import math
import os
from pathlib import Path
import random
import tempfile
import threading
import time

from .ffmpeg import FFMPEG, FFPROBE, run_process
from .video_cover import RandomCoverProcessor


_preview_slot = threading.BoundedSemaphore(1)


class PreviewBusyError(RuntimeError):
    pass


def _check_cancelled(cancelled):
    if cancelled and cancelled():
        raise InterruptedError('预览已取消')


def _choose_source(paths, rng, cancelled):
    if not paths:
        raise ValueError('请先选择素材目录')
    deadline = time.monotonic() + 3
    seen, scanned, selected = 0, 0, None
    roots = set()
    for raw in paths:
        _check_cancelled(cancelled)
        if not str(raw).strip():
            raise ValueError('请先选择素材目录')
        path = Path(raw).resolve()
        key = os.path.normcase(str(path))
        if key in roots:
            continue
        roots.add(key)
        if not path.exists():
            raise ValueError('素材目录或文件不存在，请重新选择')
        if path.is_file():
            candidates = iter([path])
        else:
            def walk_files():
                for directory, _, names in os.walk(path):
                    _check_cancelled(cancelled)
                    for name in names:
                        yield Path(directory) / name
            candidates = walk_files()
        for file in candidates:
            _check_cancelled(cancelled)
            scanned += 1
            if scanned > 50000 or time.monotonic() > deadline:
                raise ValueError('素材目录较大，请选择更具体的目录进行预览')
            if file.suffix.lower() not in ('.mp4', '.mov') or not file.is_file():
                continue
            seen += 1
            # Reservoir sampling avoids retaining thousands of paths in memory.
            if rng.randrange(seen) == 0:
                selected = file
    if selected is None:
        raise ValueError('所选目录中没有可用 MP4 / MOV 视频')
    return selected


def generate_framing_preview(paths, is_cancelled=None):
    if not _preview_slot.acquire(blocking=False):
        raise PreviewBusyError('另一个预览正在取帧，请稍后重试')
    try:
        rng = random.SystemRandom()  # Never consume the mixing/usage RNG.
        source = _choose_source(paths, rng, is_cancelled)
        try:
            info = json.loads(RandomCoverProcessor._probe_output([
                FFPROBE, '-v', 'error', '-show_entries',
                'format=duration:stream=index,codec_type,duration:stream_disposition=attached_pic',
                '-of', 'json', str(source),
            ], is_cancelled, timeout=5))
        except InterruptedError:
            raise
        except (OSError, RuntimeError, TimeoutError, ValueError):
            raise ValueError('无法读取这条素材，请检查文件或换一帧') from None
        video = next((s for s in info.get('streams', []) if s.get('codec_type') == 'video'
                      and not (s.get('disposition') or {}).get('attached_pic')), None)
        if video is None:
            raise ValueError('这条素材没有可用视频画面，请换一帧')
        try:
            duration = float(video.get('duration') or info.get('format', {}).get('duration') or 0)
        except (ValueError, TypeError):
            duration = 0
        timestamp = rng.random() * max(0, duration - .2) if math.isfinite(duration) else 0
        with tempfile.TemporaryDirectory(prefix='vm-framing-preview-') as directory:
            target = Path(directory) / 'frame.jpg'
            # Only one small JPEG. The browser handles all later zoom changes.
            for sample_time in (timestamp, 0):
                _check_cancelled(is_cancelled)
                command = [
                    FFMPEG, '-nostdin', '-v', 'error', '-y', '-ss', f'{sample_time:.9f}',
                    '-an', '-sn', '-dn', '-threads', '1', '-i', str(source),
                    '-map', f"0:{video['index']}", '-filter_threads', '1',
                    '-vf', 'scale=960:960:force_original_aspect_ratio=decrease:force_divisible_by=2,setsar=1',
                    '-frames:v', '1', '-c:v', 'mjpeg', '-q:v', '4', '-pix_fmt', 'yuvj420p',
                    '-threads:v', '1', '-update', '1', str(target),
                ]
                ok, error = run_process(command, is_cancelled, timeout=8)
                _check_cancelled(is_cancelled)
                if not ok:
                    raise ValueError('预览取帧超时，请重试或换素材' if 'timeout' in (error or '').lower()
                                     else '预览取帧失败，请检查素材或重试')
                if target.exists() and 0 < target.stat().st_size <= 2 * 1024 * 1024:
                    return dict(image_url='data:image/jpeg;base64,' + base64.b64encode(target.read_bytes()).decode('ascii'),
                                file_path=str(source), time_seconds=sample_time)
                if sample_time == 0:
                    break
            raise ValueError('这条素材没有取到可用画面，请换一帧')
    finally:
        _preview_slot.release()
