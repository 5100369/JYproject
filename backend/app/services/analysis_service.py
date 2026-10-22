"""批量素材分析任务：扫描目录、逐个分析、维护进度。

同一时间只允许一个分析任务，避免把 CPU 和 API 配额打满。
"""

from __future__ import annotations

import os
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

from ..core.analyzer import pipeline, store

VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".avi", ".webm", ".m4v", ".flv", ".wmv")


class AnalysisBusyError(RuntimeError):
    """已有分析任务在跑。"""


def _collect(directory: str, recursive: bool) -> List[str]:
    if os.path.isfile(directory):
        return [directory] if directory.lower().endswith(VIDEO_EXTS) else []
    files: List[str] = []
    if recursive:
        for root, _, names in os.walk(directory):
            for name in names:
                if name.lower().endswith(VIDEO_EXTS):
                    files.append(os.path.join(root, name))
    else:
        for name in os.listdir(directory):
            full = os.path.join(directory, name)
            if os.path.isfile(full) and name.lower().endswith(VIDEO_EXTS):
                files.append(full)
    files.sort()
    return files


class AnalysisService:
    def __init__(self) -> None:
        self._jobs: Dict[str, dict] = {}
        self._lock = threading.Lock()
        self._current: Optional[str] = None

    # ── 对外 ────────────────────────────────────────────────────────────
    def start(self, payload: dict) -> dict:
        directory = str(payload.get("directory") or "").strip()
        if not directory or not os.path.exists(directory):
            raise ValueError("目录不存在")

        recursive = bool(payload.get("recursive", True))
        files = _collect(directory, recursive)
        limit = int(payload.get("limit") or 0)
        if limit > 0:
            files = files[:limit]
        if not files:
            raise ValueError("这个目录里没有找到视频文件")

        with self._lock:
            if self._current and self._jobs.get(self._current, {}).get("status") == "running":
                raise AnalysisBusyError("已有分析任务在进行，请等它结束或先停止")
            job_id = uuid.uuid4().hex[:12]
            self._jobs[job_id] = {
                "id": job_id,
                "status": "running",
                "directory": directory,
                "total": len(files),
                "done": 0,
                "stage": "准备中",
                "stage_done": 0,
                "stage_total": 0,
                "current": "",
                "files": [],
                "errors": [],
                "use_vlm": bool(payload.get("use_vlm")),
                "started_at": datetime.now().strftime("%H:%M:%S"),
                "finished_at": "",
                "cancelled": False,
            }
            self._current = job_id
            job = self._jobs[job_id]
            cancel = threading.Event()
            job["_cancel"] = cancel

        options = {
            "use_vlm": bool(payload.get("use_vlm")),
            "max_shots": int(payload.get("max_shots") or 40),
            "sample_fps": float(payload.get("sample_fps") or 2.0),
            "scene_threshold": float(payload.get("scene_threshold") or 10.0),
        }
        threading.Thread(target=self._run, args=(job_id, files, options, cancel),
                         name="videomatrix-analysis", daemon=True).start()
        return self.public(job_id)

    def stop(self, job_id: str) -> bool:
        job = self._jobs.get(job_id)
        if not job:
            return False
        job["_cancel"].set()
        job["status"] = "stopping"
        return True

    def get(self, job_id: str) -> Optional[dict]:
        return self.public(job_id) if job_id in self._jobs else None

    def latest(self) -> Optional[dict]:
        with self._lock:
            if not self._jobs:
                return None
            job_id = max(self._jobs, key=lambda key: self._jobs[key].get("started_at", ""))
        return self.public(job_id)

    def public(self, job_id: str) -> Optional[dict]:
        job = self._jobs.get(job_id)
        if not job:
            return None
        payload = {key: value for key, value in job.items() if not key.startswith("_")}
        return payload

    # ── 内部 ────────────────────────────────────────────────────────────
    def _run(self, job_id: str, files: List[str], options: dict,
             cancel: threading.Event) -> None:
        job = self._jobs[job_id]
        try:
            for index, path in enumerate(files, 1):
                if cancel.is_set():
                    job["status"] = "stopped"
                    break
                job["current"] = os.path.basename(path)
                job["stage"] = "分析中"
                job["stage_done"] = 0
                job["stage_total"] = 0

                def progress(stage: str, done: int, total: int) -> None:
                    job["stage"] = {"scan": "切镜体检", "thumb": "抽帧",
                                    "vlm": "语义识别", "done": "收尾"}.get(stage, stage)
                    job["stage_done"] = done
                    job["stage_total"] = total

                try:
                    result = pipeline.analyze_file(
                        path,
                        use_vlm=options["use_vlm"],
                        max_shots=options["max_shots"],
                        sample_fps=options["sample_fps"],
                        scene_threshold=options["scene_threshold"],
                        progress=progress,
                        cancel=cancel,
                    )
                    key = store.cache_key(path)
                    job["files"].append({
                        "name": result["name"],
                        "path": path,
                        "key": key,
                        "duration": result["duration"],
                        "shot_count": result["summary"]["shot_count"],
                        "avg_score": result["summary"]["avg_score"],
                        "best_score": result["summary"]["best_score"],
                        "hook_count": result["summary"]["hook_count"],
                        "vlm_used": result["vlm_used"],
                        "vlm_errors": result["vlm_errors"],
                        "error": "",
                    })
                except InterruptedError:
                    job["status"] = "stopped"
                    break
                except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响整批
                    job["errors"].append({"name": os.path.basename(path), "error": str(exc)[:200]})
                    job["files"].append({
                        "name": os.path.basename(path), "path": path, "key": "",
                        "duration": 0, "shot_count": 0, "avg_score": 0, "best_score": 0,
                        "hook_count": 0, "vlm_used": False, "vlm_errors": 0,
                        "error": str(exc)[:200],
                    })
                job["done"] = index

            if job["status"] == "running":
                job["status"] = "completed"
            elif job["status"] == "stopping":
                job["status"] = "stopped"
        except Exception as exc:  # noqa: BLE001
            job["status"] = "failed"
            job["errors"].append({"name": "", "error": str(exc)[:200]})
        finally:
            job["finished_at"] = datetime.now().strftime("%H:%M:%S")
            job["stage"] = "已完成" if job["status"] == "completed" else job["stage"]
            with self._lock:
                if self._current == job_id:
                    self._current = None


analysis_service = AnalysisService()
