import os
import re
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, StreamingResponse
import asyncio
import json

from ..models.schemas import (
    CreateTaskRequest, CreateTaskResponse, StopTaskRequest,
    TaskStatus, ScanRequest, ScanResponse, ProbeResult, VideoConfig, FramingPreviewRequest,
    ScheduleCreate, ScheduleUpdate, LlmSettingsPayload, LlmGenerateRequest, LlmSrtRequest,
    AnalyzeRequest,
)
from ..services.task_service import task_service, TaskBusyError
from ..services.scheduler_service import scheduler_service
from ..services import llm_service
from ..services.analysis_service import analysis_service, AnalysisBusyError
from ..core.analyzer import store as analysis_store
from ..core.ffmpeg import probe_media, extract_media_info, extract_audio_duration

router = APIRouter()


@router.post('/framing/preview')
async def framing_preview(payload: FramingPreviewRequest, request: Request):
    import threading
    from ..core.framing_preview import generate_framing_preview, PreviewBusyError
    cancelled = threading.Event()
    worker = asyncio.create_task(asyncio.to_thread(generate_framing_preview, payload.paths, cancelled.is_set))
    try:
        while not worker.done():
            if await request.is_disconnected():
                cancelled.set()
                raise HTTPException(status_code=499, detail='预览已取消')
            await asyncio.wait({worker}, timeout=.15)
        return worker.result()
    except PreviewBusyError as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except InterruptedError as exc:
        raise HTTPException(status_code=499, detail='预览已取消') from exc
    except (ValueError, OSError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    finally:
        cancelled.set()
        # A disconnected client must not leave an unobserved worker exception.
        worker.add_done_callback(lambda done: None if done.cancelled() else done.exception())


@router.post("/tasks", response_model=CreateTaskResponse)
def create_task(req: CreateTaskRequest):
    try:
        task_id = task_service.create_task(req.config)
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CreateTaskResponse(task_id=task_id, message="任务已创建")


@router.get("/tasks", response_model=list[TaskStatus])
def list_tasks():
    return task_service.get_all_tasks()


@router.get('/tasks/activity')
def task_activity():
    return task_service.get_activity()


@router.get("/tasks/{task_id}", response_model=TaskStatus)
def get_task(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return task


@router.post("/tasks/{task_id}/stop")
def stop_task(task_id: str):
    success = task_service.stop_task(task_id)
    if not success:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"message": "停止指令已发送"}


@router.post("/tasks/stop-all")
def stop_all_tasks():
    stopped = task_service.stop_all_tasks()
    return {"message": "停止指令已发送", "stopped": stopped}


@router.post('/tasks/{task_id}/resume', response_model=CreateTaskResponse)
def resume_task(task_id: str):
    try:
        success = task_service.resume_task(task_id)
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not success:
        raise HTTPException(status_code=404, detail='任务不存在')
    return CreateTaskResponse(task_id=task_id, message='已继续原任务，仅生成剩余视频')


@router.get("/tasks/{task_id}/logs")
def get_logs(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")
    return {"logs": task_service.get_logs(task_id)}


@router.get("/tasks/{task_id}/stream")
async def stream_logs(task_id: str):
    task = task_service.get_task(task_id)
    if not task:
        raise HTTPException(status_code=404, detail="任务不存在")

    async def event_generator():
        last_len = 0
        while True:
            logs = task_service.get_logs(task_id)
            if len(logs) > last_len:
                new_logs = logs[last_len:]
                for line in new_logs:
                    yield f"data: {json.dumps({'log': line}, ensure_ascii=False)}\n\n"
                last_len = len(logs)

            current = task_service.get_task(task_id)
            if current and current.status in ("completed", "partial", "failed", "stopped"):
                yield f"data: {json.dumps({'status': current.status, 'progress': current.progress}, ensure_ascii=False)}\n\n"
                yield "data: [DONE]\n\n"
                break

            await asyncio.sleep(0.5)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )


@router.post("/scan", response_model=ScanResponse)
def scan_directory(req: ScanRequest):
    if not os.path.exists(req.dir_path):
        raise HTTPException(status_code=400, detail="目录不存在")
    files = []
    if os.path.isfile(req.dir_path):
        files = [req.dir_path] if any(req.dir_path.lower().endswith(ext.lower()) for ext in req.extensions) else []
        return ScanResponse(files=files, count=len(files))
    for root, _, filenames in os.walk(req.dir_path):
        for f in filenames:
            if any(f.lower().endswith(ext) for ext in req.extensions):
                files.append(os.path.join(root, f))
    return ScanResponse(files=files, count=len(files))


@router.post("/probe", response_model=ProbeResult)
def probe_file(file_path: str):
    if not os.path.exists(file_path):
        raise HTTPException(status_code=400, detail="文件不存在")
    info = probe_media(file_path)
    if not info:
        raise HTTPException(status_code=500, detail="无法探测文件")
    dur, has_audio, width, height, fps = extract_media_info(info, file_path)
    return ProbeResult(
        file_path=file_path,
        duration=dur,
        source_duration=extract_media_info(info, file_path, safety_margin=0)[0],
        audio_duration=extract_audio_duration(info, safety_margin=0),
        has_audio=has_audio,
        width=width,
        height=height,
        fps=fps
    )


@router.post("/benchmark")
def benchmark(config: VideoConfig):
    try:
        return task_service.get_benchmark(config)
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/preflight")
def preflight(config: VideoConfig):
    return task_service.preflight(config)


@router.post("/history/clear")
def clear_history():
    task_service.clear_history()
    return {"message": "使用记录已清除"}


# ── 定时混剪 ──────────────────────────────────────────────────────────────
@router.get("/schedules")
def list_schedules():
    return scheduler_service.list()


@router.post("/schedules")
def create_schedule(req: ScheduleCreate):
    try:
        return scheduler_service.create(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.patch("/schedules/{schedule_id}")
def update_schedule(schedule_id: str, req: ScheduleUpdate):
    try:
        item = scheduler_service.update(schedule_id, req.model_dump(exclude_none=True))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if not item:
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return item


@router.delete("/schedules/{schedule_id}")
def delete_schedule(schedule_id: str):
    if not scheduler_service.delete(schedule_id):
        raise HTTPException(status_code=404, detail="定时任务不存在")
    return {"message": "已删除"}


@router.post("/schedules/{schedule_id}/run-now", response_model=CreateTaskResponse)
def run_schedule_now(schedule_id: str):
    try:
        result = scheduler_service.run_now(schedule_id)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail="定时任务不存在") from exc
    except TaskBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return CreateTaskResponse(task_id=result["task_id"], message=result["message"])


# ── LLM 文案 ──────────────────────────────────────────────────────────────
@router.get("/llm/settings")
def get_llm_settings():
    return llm_service.public_settings()


@router.post("/llm/settings")
def save_llm_settings(req: LlmSettingsPayload):
    llm_service.save_settings(req.model_dump())
    return llm_service.public_settings()


@router.post("/llm/generate")
def generate_copy(req: LlmGenerateRequest):
    try:
        return llm_service.generate(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.post("/llm/to-srt")
def copy_to_srt(req: LlmSrtRequest):
    try:
        return llm_service.write_srt(req.text, req.directory, req.filename, req.chars_per_second)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


# ── 素材画面分析 ──────────────────────────────────────────────────────────
@router.post("/analyze")
def start_analysis(req: AnalyzeRequest):
    try:
        return analysis_service.start(req.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except AnalysisBusyError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/analyze/latest")
def latest_analysis():
    return analysis_service.latest() or {}


@router.get("/analyze/{job_id}")
def get_analysis(job_id: str):
    job = analysis_service.get(job_id)
    if not job:
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return job


@router.post("/analyze/{job_id}/stop")
def stop_analysis(job_id: str):
    if not analysis_service.stop(job_id):
        raise HTTPException(status_code=404, detail="分析任务不存在")
    return {"message": "停止指令已发送"}


@router.get("/analysis/thumb/{key}/{name}")
def get_analysis_thumb(key: str, name: str):
    if not re.fullmatch(r"[0-9a-f]{16}", key) or not re.fullmatch(r"\d{3}\.jpg", name):
        raise HTTPException(status_code=400, detail="无效的缩略图地址")
    path = analysis_store.analysis_root() / "thumbs" / key / name
    if not path.is_file():
        raise HTTPException(status_code=404, detail="缩略图不存在")
    return FileResponse(str(path), media_type="image/jpeg")


@router.post("/analysis/clear")
def clear_analysis():
    return {"removed": analysis_store.clear_all()}


@router.get("/analysis/{key}")
def get_analysis_result(key: str):
    if not re.fullmatch(r"[0-9a-f]{16}", key):
        raise HTTPException(status_code=400, detail="无效的分析编号")
    data = analysis_store.load_key(key)
    if not data:
        raise HTTPException(status_code=404, detail="没有这个分析结果，可能已被清理")
    return data
