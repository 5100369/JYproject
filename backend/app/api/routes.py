import os
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
import asyncio
import json

from ..models.schemas import (
    CreateTaskRequest, CreateTaskResponse, StopTaskRequest,
    TaskStatus, ScanRequest, ScanResponse, ProbeResult, VideoConfig, FramingPreviewRequest
)
from ..services.task_service import task_service, TaskBusyError
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
