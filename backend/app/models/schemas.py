from pydantic import BaseModel, Field, model_validator
from typing import Dict, List, Optional, Literal, Union
from datetime import datetime


class BgmTrack(BaseModel):
    enabled: bool = True
    path: str = ""
    volume: float = Field(default=30, ge=0, le=200)
    fade_in: float = Field(default=0, ge=0, le=3600)
    fade_out: float = Field(default=0, ge=0, le=3600)
    short_behavior: Literal["loop", "stop"] = "loop"
    source_mode: Literal["start", "random"] = "start"
    overlap: float = Field(default=0.3, ge=0, le=1)


class BgmTracks(BaseModel):
    full: BgmTrack = Field(default_factory=BgmTrack)
    hook: BgmTrack = Field(default_factory=lambda: BgmTrack(enabled=False))
    body: BgmTrack = Field(default_factory=lambda: BgmTrack(enabled=False))


class VideoConfig(BaseModel):
    task_name: str = Field(default="Task", description="任务名称")
    hook_dir: str = Field(..., description="首段素材目录")
    body_dirs: List[str] = Field(default_factory=list, description="后段素材目录列表")
    body_mode: Literal["normal", "grouped"] = Field(default="normal", description="后段拼接模式")
    body_groups: List["BodyGroup"] = Field(default_factory=list, max_length=10, description="按顺序拼接的 Body 分组，最多 10 组")
    hook_zoom_percent: float = Field(default=100, ge=100, le=300, description="Hook 居中放大比例(%)")
    body_zoom_percent: float = Field(default=100, ge=100, le=300, description="普通及全部分组 Body 居中放大比例(%)")
    bgm_dir: str = Field(default="", description="可选 BGM 目录或带音轨的视频文件")
    bgm_tracks: Optional[BgmTracks] = Field(default=None, description="全片 / Hook / Body 三条独立配乐；未提供时兼容旧接口")
    duration_mode: Literal["clips", "bgm"] = Field(default="clips", description="按片段数量或完整 BGM 时长生成")
    voice_dir: Optional[str] = Field(default=None, description="配音目录")
    srt_dir: Optional[str] = Field(default=None, description="字幕目录")
    watermark_path: Optional[str] = Field(default=None, description="水印图片/GIF路径")
    base_out_dir: str = Field(default="", description="输出父目录")
    
    t_hook: float = Field(default=3.0, ge=0.5, description="首段时长(秒)")
    hook_full_duration: bool = Field(default=False, description="按原素材时长使用完整 Hook，随机轮换")
    t_body: float = Field(default=3.0, ge=0.5, description="后段片段时长(秒)")
    body_full_duration: bool = Field(default=False, description="普通 Body 使用完整原素材")
    total_clips: int = Field(default=5, ge=2, description="每视频总片段数")

    clip_duration_random_enabled: bool = Field(
        default=False,
        description="每个片段时长在最短/最长之间随机截取，未启用时按 t_hook / t_body 固定时长",
    )
    clip_duration_min: float = Field(default=2.0, ge=0.5, le=120, description="单片段最短时长(秒)")
    clip_duration_max: float = Field(default=4.0, ge=0.5, le=120, description="单片段最长时长(秒)")
    clip_duration_step: float = Field(default=0.1, ge=0, le=2, description="随机时长精度(秒)，0 表示保留到毫秒")
    clip_duration_scope: Literal["body", "all"] = Field(
        default="body",
        description="随机时长作用范围：body 仅后段，all 含首段",
    )
    target_count: int = Field(default=10, ge=1, description="目标生成数量")
    
    hook_r: float = Field(default=0.5, ge=0.0, le=1.0, description="首段重叠率")
    body_r: float = Field(default=0.5, ge=0.0, le=1.0, description="后段重叠率")
    bgm_r: float = Field(default=0.3, ge=0.0, le=1.0, description="BGM重叠率")
    
    resolution: str = Field(default="1080*1920", description="输出分辨率")
    fps: Union[str, float, int] = Field(default="30", description="输出帧率")
    bitrate: str = Field(default="8000k", description="视频码率")
    random_resolution_enabled: bool = Field(default=False, description="主输出随机分辨率，保持当前宽高比")
    random_resolution_min: int = Field(default=1080, description="随机分辨率短边下限(px)")
    random_resolution_max: int = Field(default=1440, description="随机分辨率短边上限(px)")
    random_bitrate_enabled: bool = Field(default=False, description="主输出随机视频目标码率")
    random_bitrate_min: int = Field(default=8000, description="随机视频目标码率下限(kbps)")
    random_bitrate_max: int = Field(default=14000, description="随机视频目标码率上限(kbps)")
    
    vol_orig: int = Field(default=80, ge=0, le=200, description="Body原声音量(%)")
    vol_hook_orig: Optional[int] = Field(default=None, ge=0, le=200, description="Hook原声音量(%)")
    vol_bgm: int = Field(default=30, ge=0, le=200, description="BGM音量(%)")
    vol_voice: int = Field(default=100, ge=0, le=200, description="配音音量(%)")

    apply_bgm_to_hook: bool = Field(default=True, description="BGM是否作用于Hook")
    apply_voice_to_hook: bool = Field(default=True, description="配音是否作用于Hook")
    apply_srt_to_hook: bool = Field(default=True, description="字幕是否作用于Hook")
    apply_watermark_to_hook: bool = Field(default=True, description="水印是否作用于Hook")
    
    enable_srt: bool = Field(default=False, description="是否启用硬字幕")
    subtitle_y_percent: float = Field(default=92.0, ge=8.0, le=92.0, description="字幕垂直位置百分比")
    subtitle_font_size_percent: float = Field(default=5.6, ge=3.0, le=9.0, description="字幕字号占画面高度百分比")
    subtitle_auto_split: bool = Field(
        default=True,
        description="自动把长字幕切成短句，并按字数重排时间轴",
    )
    subtitle_max_chars: int = Field(default=15, ge=6, le=40, description="单条字幕最大字数")
    subtitle_min_chars: int = Field(default=4, ge=1, le=20, description="短于该字数时尝试与相邻字幕合并")
    subtitle_ai_split: bool = Field(
        default=True,
        description="用 AI 做语义断句，避免把词语、品牌名切断；接口失败自动回退规则断句",
    )
    enable_gpu: bool = Field(default=True, description="自动选择可用硬件编码；不可用时继续使用CPU")
    concurrent_tasks: int = Field(default=3, ge=1, le=16, description="并发渲染数")

    enable_variants: bool = Field(default=False, description="启用独立画面变换层，与混剪共用一次编码")
    variant_strength: Literal["mild", "balanced", "strong"] = Field(default="balanced", description="变体强度")
    variant_hook: bool = Field(default=True, description="变体作用于 Hook")
    variant_body: bool = Field(default=True, description="变体作用于 Body")
    variant_mirror: bool = Field(default=False, description="允许随机镜像")
    variant_crop: bool = Field(default=True, description="微裁切，按强度最多裁去每方向两侧合计 2%")
    variant_color: bool = Field(default=False, description="显式启用轻微色彩调整")
    variant_frame_mix: bool = Field(default=False, description="旧配置兼容字段，V2 不再执行帧混合")
    variant_blend_enabled: bool = Field(default=False, description="显式启用 B 画面混合，会增加解码与滤镜耗时")
    variant_blend_path: str = ""
    variant_blend_opacity: float = Field(default=0.03, ge=0.01, le=0.15)
    variant_blend_eof: Literal["loop", "freeze", "error"] = "loop"
    variant_protected_regions: List[dict] = Field(default_factory=list, max_length=64, description="基础画面归一化保护区域，含 x/y/width/height 及可选 start/end")
    variant_seed: Optional[int] = Field(default=None, ge=0, description="任务级随机种子")
    enable_random_cover: bool = Field(default=False, description="随机替换成片首帧")
    random_cover_mode: Literal["replace", "insert"] = Field(default="replace", description="随机封面首帧处理方式")

    output_name_template: str = Field(
        default="{name}_{i:03d}",
        description="输出文件名模板，支持 {name}/{i:03d}/{date}/{time}/{datetime}/{rand}/{resolution}",
    )
    export_jianying_draft: bool = Field(default=True, description="出片同时导出对应剪映草稿")
    jianying_draft_dir: str = Field(default="", description="剪映草稿箱目录；留空自动探测")

    smart_select_enabled: bool = Field(
        default=False,
        description="按素材分析结果选片（按镜头边界切、按高分优先），未分析过的素材自动回退等间隔切片",
    )
    smart_select_min_score: float = Field(
        default=0, ge=0, le=100,
        description="智能选片时低于此分数的镜头直接跳过，0 表示不过滤",
    )
    smart_select_hook_top: int = Field(
        default=12, ge=1, le=200,
        description="智能选片时 Hook 只从分数最高的前 N 个镜头里抽，保证开头质量",
    )

    @model_validator(mode='before')
    @classmethod
    def ignore_inactive_output_ranges(cls, values):
        if isinstance(values, dict):
            from ..core.output_settings import OUTPUT_DEFAULTS
            values = values.copy()
            for prefix in ('random_resolution', 'random_bitrate'):
                flag = values.get(f'{prefix}_enabled', False)
                inactive = flag is None or flag is False or flag == 0 or (
                    isinstance(flag, str) and flag.lower() in ('false', 'f', '0', 'off', 'no', 'n')
                )
                if inactive:
                    for suffix in ('min', 'max'):
                        key = f'{prefix}_{suffix}'
                        values[key] = OUTPUT_DEFAULTS[key]
        return values

    @model_validator(mode='after')
    def check_output_ranges(self):
        from ..core.output_settings import validate_output_settings
        validate_output_settings(self.model_dump())
        return self


class FramingPreviewRequest(BaseModel):
    paths: List[str] = Field(min_length=1, max_length=128)


class BodyGroup(BaseModel):
    full_duration: bool = Field(default=False, description="本组使用完整原素材")
    enabled: bool = Field(default=True, description="是否使用此组")
    folder: str = Field(default="", description="此组素材目录")
    clip_count: int = Field(default=1, ge=1, description="此组抽取片段数")
    clip_duration: float = Field(default=3.0, ge=0.5, description="此组片段时长(秒)")


class TaskStatus(BaseModel):
    task_id: str
    task_name: str
    status: Literal["pending", "running", "completed", "partial", "failed", "stopped"]
    progress: int = Field(default=0, ge=0, le=100)
    current: int = Field(default=0, ge=0)
    total: int = Field(default=0, ge=0)
    message: str = ""
    log_lines: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None
    output_files: List[str] = Field(default_factory=list)
    output_elapsed: Dict[str, float] = Field(default_factory=dict)
    output_warnings: Dict[str, str] = Field(default_factory=dict)
    acceleration: str = ""
    acceleration_warning: str = ""
    effective_concurrency: int = 0
    stopping: bool = False
    can_resume: bool = False
    resume_remaining: int = Field(default=0, ge=0)


class ProbeResult(BaseModel):
    file_path: str
    duration: float
    source_duration: float = 0
    audio_duration: float = 0
    has_audio: bool
    width: Optional[int] = None
    height: Optional[int] = None
    fps: Optional[float] = None


class ScanRequest(BaseModel):
    dir_path: str
    extensions: List[str] = Field(default_factory=lambda: [".mp4", ".mov"])


class ScanResponse(BaseModel):
    files: List[str]
    count: int


class CreateTaskRequest(BaseModel):
    config: VideoConfig


class CreateTaskResponse(BaseModel):
    task_id: str
    message: str


class StopTaskRequest(BaseModel):
    task_id: str


class ScheduleCreate(BaseModel):
    name: str = Field(default="定时任务", description="定时任务名称")
    config: VideoConfig = Field(description="到点执行时使用的完整混剪配置快照")
    trigger_type: Literal["once", "daily", "weekly", "interval"] = Field(default="once")
    time_of_day: str = Field(default="09:00", description="每天/每周的执行时刻 HH:MM")
    weekdays: List[int] = Field(default_factory=list, description="每周执行日，1=周一 … 7=周日")
    run_at: str = Field(default="", description="单次执行时间 YYYY-MM-DDTHH:MM")
    interval_minutes: int = Field(default=60, ge=1, le=10080, description="间隔执行的分钟数")
    grace_minutes: int = Field(default=30, ge=0, le=1440, description="错过执行点后的补跑窗口")


class ScheduleUpdate(BaseModel):
    name: Optional[str] = None
    enabled: Optional[bool] = None
    time_of_day: Optional[str] = None
    weekdays: Optional[List[int]] = None
    run_at: Optional[str] = None
    interval_minutes: Optional[int] = Field(default=None, ge=1, le=10080)
    grace_minutes: Optional[int] = Field(default=None, ge=0, le=1440)


class LlmSettingsPayload(BaseModel):
    base_url: Optional[str] = None
    api_key: Optional[str] = None
    model: Optional[str] = None
    temperature: Optional[float] = None
    clear_key: bool = False


class LlmGenerateRequest(BaseModel):
    brief: str = Field(description="产品信息 / 卖点")
    count: int = Field(default=3, ge=1, le=10)
    style: str = Field(default="", description="风格要求")
    duration: float = Field(default=30, ge=5, le=180, description="单条时长(秒)")


class LlmSrtRequest(BaseModel):
    text: str = Field(min_length=1)
    directory: str = Field(description="字幕目录")
    filename: str = ""
    chars_per_second: float = Field(default=4.5, ge=1, le=12)


class AnalyzeRequest(BaseModel):
    directory: str = Field(description="要分析的素材目录")
    recursive: bool = True
    use_vlm: bool = Field(default=False, description="是否调用视觉模型做语义理解")
    limit: int = Field(default=0, ge=0, le=500, description="最多分析多少个文件，0 表示不限")
    max_shots: int = Field(default=40, ge=5, le=200, description="每个视频最多保留多少镜头")
    sample_fps: float = Field(default=2.0, ge=0.5, le=10)
    scene_threshold: float = Field(default=10.0, ge=1, le=100)
