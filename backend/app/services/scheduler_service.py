"""定时混剪调度。

用户在界面上排一个时间点，到点由本服务把当时保存的配置快照提交给
TaskService，等同于手动点一次「启动渲染」。任务本身仍由 TaskService
在后台线程里跑，所以窗口最小化到托盘不影响执行。

触发方式：
- once      单次，指定日期时间
- daily     每天固定时刻
- weekly    每周指定几天 + 固定时刻
- interval  每隔 N 分钟

所有时间都是本机本地时间。错过执行点后仍在 grace_minutes 内会补跑，
超过则记为错过（单次任务自动停用，周期任务顺延到下一次）。
"""

from __future__ import annotations

import json
import os
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from ..models.schemas import VideoConfig
from .task_service import TaskBusyError, task_service

WEEKDAY_LABELS = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
TRIGGER_TYPES = ("once", "daily", "weekly", "interval")

# 设备忙时，隔多久再试一次（秒）
RETRY_INTERVAL = 30
# 调度轮询间隔（秒）
TICK_INTERVAL = 2


def state_dir() -> Path:
    root = Path(os.environ.get("APPDATA") or os.path.expanduser("~")) / "VideoMatrix"
    root.mkdir(parents=True, exist_ok=True)
    return root


def parse_dt(value: str) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _iso(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def clean_weekdays(values) -> List[int]:
    cleaned = []
    for value in values or []:
        try:
            day = int(value)
        except (TypeError, ValueError):
            continue
        if 1 <= day <= 7 and day not in cleaned:
            cleaned.append(day)
    return sorted(cleaned)


def _hhmm(value: str) -> tuple[int, int]:
    try:
        hour, minute = str(value).split(":")[:2]
        hour, minute = int(hour), int(minute)
    except (TypeError, ValueError):
        return 9, 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return 9, 0
    return hour, minute


@dataclass
class Schedule:
    id: str
    name: str
    config: dict
    trigger_type: str = "once"
    time_of_day: str = "09:00"
    weekdays: List[int] = field(default_factory=list)   # 1=周一 … 7=周日
    run_at: str = ""                                    # once 用，YYYY-MM-DDTHH:MM
    interval_minutes: int = 60
    enabled: bool = True
    grace_minutes: int = 30
    next_run: str = ""
    last_run: str = ""
    last_task_id: str = ""
    last_result: str = ""      # started / busy / missed / error / ""
    last_message: str = ""
    run_count: int = 0
    created_at: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict) -> "Schedule":
        allowed = {f for f in cls.__dataclass_fields__}
        return cls(**{k: v for k, v in raw.items() if k in allowed})

    # ── 下次执行时间 ─────────────────────────────────────────────────────
    def compute_next(self, now: Optional[datetime] = None) -> str:
        now = now or datetime.now()
        kind = self.trigger_type

        if kind == "once":
            return self.run_at

        if kind == "interval":
            base = parse_dt(self.last_run) or now
            step = max(1, int(self.interval_minutes or 60))
            nxt = base + timedelta(minutes=step)
            # 停机很久后补齐没有意义，从当下重新起算
            if nxt <= now:
                nxt = now + timedelta(minutes=step)
            return _iso(nxt)

        hour, minute = _hhmm(self.time_of_day)

        if kind == "daily":
            candidate = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
            if candidate <= now:
                candidate += timedelta(days=1)
            return _iso(candidate)

        # weekly
        days = clean_weekdays(self.weekdays) or [1]
        for offset in range(0, 8):
            candidate = (now + timedelta(days=offset)).replace(
                hour=hour, minute=minute, second=0, microsecond=0
            )
            if candidate > now and candidate.isoweekday() in days:
                return _iso(candidate)
        return ""

    def due_moment(self) -> Optional[datetime]:
        return parse_dt(self.next_run)


class SchedulerService:
    """单例调度器。所有对 self._schedules 的读写都走 self._lock。"""

    def __init__(self, state_file: Optional[Path] = None):
        self.state_file = state_file or (state_dir() / "schedule_state.json")
        self._lock = threading.RLock()
        self._schedules: Dict[str, Schedule] = {}
        self._retry_at: Dict[str, float] = {}
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    # ── 生命周期 ─────────────────────────────────────────────────────────
    def start(self) -> None:
        with self._lock:
            if self._thread and self._thread.is_alive():
                return
            self.load()
            self._stop.clear()
            self._thread = threading.Thread(target=self._loop, name="videomatrix-scheduler", daemon=True)
            self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _loop(self) -> None:
        while not self._stop.wait(TICK_INTERVAL):
            try:
                self.tick()
            except Exception:  # noqa: BLE001 - 调度线程不能因为单条异常退出
                continue

    # ── 持久化 ───────────────────────────────────────────────────────────
    def load(self) -> None:
        with self._lock:
            self._schedules.clear()
            if not self.state_file.exists():
                return
            try:
                raw = json.loads(self.state_file.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                return
            now = datetime.now()
            for item in raw.get("schedules", []):
                try:
                    schedule = Schedule.from_dict(item)
                except (TypeError, ValueError):
                    continue
                if not schedule.id:
                    continue
                # 停机期间错过的周期任务：窗口内保留以便补跑，超出则顺延
                if schedule.enabled and schedule.trigger_type != "once":
                    due = parse_dt(schedule.next_run)
                    window = timedelta(minutes=max(0, schedule.grace_minutes))
                    if not due or (now - due) > window:
                        schedule.next_run = schedule.compute_next(now)
                self._schedules[schedule.id] = schedule

    def _save_locked(self) -> None:
        payload = {
            "version": 1,
            "saved_at": _iso(datetime.now()),
            "schedules": [s.to_dict() for s in self._schedules.values()],
        }
        try:
            self.state_file.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.state_file.with_suffix(".tmp")
            tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            os.replace(tmp, self.state_file)
        except OSError:
            pass

    # ── 查询 ─────────────────────────────────────────────────────────────
    def list(self) -> List[dict]:
        with self._lock:
            items = [s.to_dict() for s in self._schedules.values()]
        items.sort(key=lambda s: (not s["enabled"], s["next_run"] or "9999", s["created_at"]))
        return items

    def get(self, schedule_id: str) -> Optional[Schedule]:
        with self._lock:
            return self._schedules.get(schedule_id)

    # ── 增删改 ───────────────────────────────────────────────────────────
    def create(self, payload: dict) -> dict:
        trigger_type = payload.get("trigger_type", "once")
        if trigger_type not in TRIGGER_TYPES:
            raise ValueError("不支持的触发方式")

        config = payload.get("config") or {}
        if not config.get("hook_dir"):
            raise ValueError("配置里缺少首段素材目录，请先在「混剪」页选好素材")

        now = datetime.now()
        schedule = Schedule(
            id=uuid.uuid4().hex[:12],
            name=(payload.get("name") or "定时任务").strip() or "定时任务",
            config=config,
            trigger_type=trigger_type,
            time_of_day=payload.get("time_of_day") or "09:00",
            weekdays=clean_weekdays(payload.get("weekdays")),
            run_at=(payload.get("run_at") or "").strip(),
            interval_minutes=int(payload.get("interval_minutes") or 60),
            grace_minutes=max(0, min(1440, int(payload.get("grace_minutes") or 30))),
            created_at=_iso(now),
        )

        if trigger_type == "once":
            run_at = parse_dt(schedule.run_at)
            if not run_at:
                raise ValueError("单次任务需要填写执行时间")
            if run_at <= now:
                raise ValueError("执行时间已过，请选一个将来的时间")
            schedule.next_run = _iso(run_at)
        elif trigger_type == "weekly":
            if not schedule.weekdays:
                raise ValueError("每周任务至少要选一天")
            schedule.next_run = schedule.compute_next(now)
        elif trigger_type == "interval":
            schedule.next_run = _iso(now + timedelta(minutes=max(1, schedule.interval_minutes)))
        else:
            schedule.next_run = schedule.compute_next(now)

        with self._lock:
            self._schedules[schedule.id] = schedule
            self._save_locked()
            return schedule.to_dict()

    def update(self, schedule_id: str, patch: dict) -> Optional[dict]:
        with self._lock:
            schedule = self._schedules.get(schedule_id)
            if not schedule:
                return None

            timing_changed = False
            for key in ("name", "time_of_day", "run_at", "interval_minutes", "grace_minutes"):
                if patch.get(key) is not None:
                    setattr(schedule, key, patch[key])
                    timing_changed = timing_changed or key in ("time_of_day", "run_at", "interval_minutes")
            if patch.get("weekdays") is not None:
                schedule.weekdays = clean_weekdays(patch["weekdays"])
                timing_changed = True
            if patch.get("enabled") is not None:
                enable = bool(patch["enabled"])
                if enable and not schedule.enabled:
                    now = datetime.now()
                    due = schedule.due_moment()
                    # 单次任务过期后不能用旧时间重新启用，必须显式改时间
                    if schedule.trigger_type == "once" and (not due or due <= now):
                        raise ValueError("这个单次任务的时间已经过去，请修改执行时间后再启用")
                    if schedule.trigger_type != "once" and (not due or due <= now):
                        timing_changed = True
                schedule.enabled = enable
                if not enable:
                    self._retry_at.pop(schedule.id, None)

            if timing_changed and schedule.enabled:
                now = datetime.now()
                if schedule.trigger_type == "once":
                    run_at = parse_dt(schedule.run_at)
                    if not run_at:
                        raise ValueError("单次任务需要填写执行时间")
                    if run_at <= now:
                        raise ValueError("执行时间已过，请选一个将来的时间")
                    schedule.next_run = _iso(run_at)
                elif schedule.trigger_type == "weekly" and not schedule.weekdays:
                    raise ValueError("每周任务至少要选一天")
                else:
                    schedule.next_run = schedule.compute_next(now)
                self._retry_at.pop(schedule.id, None)

            self._save_locked()
            return schedule.to_dict()

    def delete(self, schedule_id: str) -> bool:
        with self._lock:
            if schedule_id not in self._schedules:
                return False
            del self._schedules[schedule_id]
            self._retry_at.pop(schedule_id, None)
            self._save_locked()
            return True

    # ── 执行 ─────────────────────────────────────────────────────────────
    def run_now(self, schedule_id: str) -> dict:
        """立即执行一次，但不改变原有的下次执行时间。"""
        with self._lock:
            schedule = self._schedules.get(schedule_id)
            if not schedule:
                raise KeyError("定时任务不存在")
            task_id = self._launch(schedule, immediate=True)
            self._save_locked()
            return {"task_id": task_id, "message": "已启动"}

    def tick(self) -> None:
        now = datetime.now()
        with self._lock:
            pending = [s for s in self._schedules.values() if s.enabled and s.next_run]
        for schedule in pending:
            try:
                self._check(schedule, now)
            except Exception:  # noqa: BLE001
                continue

    def _check(self, schedule: Schedule, now: datetime) -> None:
        due = schedule.due_moment()
        if not due or now < due:
            return

        window = timedelta(minutes=max(0, schedule.grace_minutes))
        if now - due > window:
            with self._lock:
                label = due.strftime("%m-%d %H:%M")
                if schedule.trigger_type == "once":
                    schedule.enabled = False
                    schedule.next_run = ""
                    schedule.last_result = "missed"
                    schedule.last_message = f"错过执行时间 {label}，已停用"
                else:
                    schedule.next_run = schedule.compute_next(now)
                    schedule.last_result = "missed"
                    schedule.last_message = f"错过执行时间 {label}，已顺延到下一次"
                self._save_locked()
            return

        # 设备忙时段隔 30 秒再试，避免每 2 秒刷一次任务表
        if time.time() < self._retry_at.get(schedule.id, 0):
            return

        with self._lock:
            try:
                self._launch(schedule, immediate=False)
                self._retry_at.pop(schedule.id, None)
                self._save_locked()
            except TaskBusyError:
                self._retry_at[schedule.id] = time.time() + RETRY_INTERVAL
                schedule.last_result = "busy"
                schedule.last_message = "设备正在执行其他任务，等待空档…"
                self._save_locked()
            except Exception as exc:  # noqa: BLE001
                self._retry_at.pop(schedule.id, None)
                schedule.last_result = "error"
                schedule.last_message = str(exc)[:200]
                if schedule.trigger_type == "once":
                    schedule.enabled = False
                    schedule.next_run = ""
                else:
                    schedule.next_run = schedule.compute_next(now)
                self._save_locked()

    def _launch(self, schedule: Schedule, immediate: bool) -> str:
        """提交任务。调用方需持有 self._lock。"""
        config = VideoConfig(**schedule.config)
        task_id = task_service.create_task(config)
        now = datetime.now()
        schedule.last_run = _iso(now)
        schedule.last_task_id = task_id
        schedule.last_result = "started"
        schedule.last_message = ("手动立即执行" if immediate else "已按计划启动") + f"，任务 {task_id[:8]}"
        schedule.run_count += 1
        if not immediate:
            if schedule.trigger_type == "once":
                schedule.enabled = False
                schedule.next_run = ""
            else:
                schedule.next_run = schedule.compute_next(now)
        return task_id


scheduler_service = SchedulerService()
