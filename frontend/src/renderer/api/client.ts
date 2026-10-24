const getBaseUrl = async (): Promise<string> => {
  if (window.electronAPI) {
    const port = await window.electronAPI.getBackendPort()
    return `http://127.0.0.1:${port}/api`
  }
  return 'http://127.0.0.1:8765/api'
}

function formatApiError(detail: unknown, fallback: string): string {
  const fieldLabels: Record<string, string> = {
    hook_r: 'Hook 重叠率', body_r: 'Body 重叠率', bgm_r: 'BGM 重叠率',
    hook_zoom_percent: 'Hook 放大比例', body_zoom_percent: 'Body 放大比例',
    t_hook: '首段时长', t_body: '后段时长', total_clips: '片段数',
    target_count: '生成数量', concurrent_tasks: '并发数', vol_hook_orig: 'Hook 原声音量',
    random_resolution_min: '随机分辨率短边下限', random_resolution_max: '随机分辨率短边上限',
    random_bitrate_min: '随机码率下限', random_bitrate_max: '随机码率上限',
    full: '全片 BGM', hook: 'Hook BGM', body: 'Body BGM',
    volume: '音量', fade_in: '渐入秒数', fade_out: '渐出秒数', overlap: '裁切重叠率', path: '配乐路径',
  }
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    const messages = detail
      .map((item) => {
        if (!item || typeof item !== 'object') return null
        const error = item as { loc?: unknown[]; msg?: string }
        const field = error.loc?.at(-1)
        const scope = error.loc?.includes('bgm_tracks') ? error.loc.find(value => ['full', 'hook', 'body'].includes(String(value))) : null
        const label = (scope ? `${fieldLabels[String(scope)]} ` : '') + (field ? fieldLabels[String(field)] || String(field) : '')
        return label && error.msg ? `${label}：${error.msg}` : error.msg
      })
      .filter((message): message is string => Boolean(message))
    if (messages.length) return messages.join('；')
  }
  return fallback
}

async function request<T>(path: string, options?: RequestInit): Promise<T> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}${path}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) {
    const err = await res.json().catch(() => ({ detail: 'Unknown error' }))
    throw new Error(formatApiError(err.detail, `请求失败（HTTP ${res.status}）`))
  }
  return res.json()
}

export interface BodyGroup {
  full_duration?: boolean
  enabled: boolean
  folder: string
  clip_count: string | number
  clip_duration: string | number
}

export type BgmScope = 'full' | 'hook' | 'body'
export interface BgmTrack {
  enabled: boolean
  path: string
  volume: string | number
  fade_in: string | number
  fade_out: string | number
  short_behavior: 'loop' | 'stop'
  source_mode: 'start' | 'random'
  overlap: string | number
}
export type BgmTracks = Record<BgmScope, BgmTrack>

export interface VideoConfig {
  task_name: string
  hook_dir: string
  body_dirs: string[]
  body_mode: 'normal' | 'grouped'
  body_groups: BodyGroup[]
  hook_zoom_percent: string | number
  body_zoom_percent: string | number
  bgm_dir: string
  bgm_tracks?: BgmTracks
  finished_hook?: boolean
  duration_mode: 'clips' | 'bgm'
  voice_dir?: string
  srt_dir?: string
  watermark_path?: string
  base_out_dir: string
  t_hook: string | number
  hook_full_duration: boolean
  t_body: string | number
  body_full_duration?: boolean
  total_clips: string | number
  target_count: string | number
  hook_r: string | number
  body_r: string | number
  bgm_r: string | number
  resolution: string
  fps: string | number
  bitrate: string
  random_resolution_enabled: boolean
  random_resolution_min: string | number
  random_resolution_max: string | number
  random_bitrate_enabled: boolean
  random_bitrate_min: string | number
  random_bitrate_max: string | number
  vol_orig: string | number
  vol_hook_orig: string | number
  vol_bgm: string | number
  vol_voice: string | number
  apply_bgm_to_hook: boolean
  apply_voice_to_hook: boolean
  apply_srt_to_hook: boolean
  apply_watermark_to_hook: boolean
  enable_srt: boolean
  subtitle_y_percent: number
  subtitle_font_size_percent: number
  subtitle_auto_split?: boolean
  subtitle_max_chars?: string | number
  subtitle_min_chars?: string | number
  subtitle_ai_split?: boolean
  enable_gpu: boolean
  concurrent_tasks?: string | number
  enable_variants: boolean
  variant_strength: 'mild' | 'balanced' | 'strong'
  variant_hook: boolean
  variant_body: boolean
  variant_crop: boolean
  variant_color: boolean
  variant_mirror: boolean
  variant_frame_mix: boolean
  variant_seed?: number | null
  variant_blend_enabled: boolean
  variant_blend_path: string
  variant_blend_opacity: string | number
  variant_blend_eof: 'loop' | 'freeze' | 'error'
  enable_random_cover: boolean
  random_cover_mode: 'replace' | 'insert'
  output_name_template?: string
  export_jianying_draft?: boolean
  jianying_draft_dir?: string
  smart_select_enabled?: boolean
  smart_select_min_score?: string | number
  smart_select_hook_top?: string | number
  clip_duration_random_enabled?: boolean
  clip_duration_min?: string | number
  clip_duration_max?: string | number
  clip_duration_step?: string | number
  clip_duration_scope?: 'body' | 'all'
}

const NUMERIC_CONFIG_FIELDS = [
  't_hook', 't_body', 'total_clips', 'target_count', 'hook_r', 'body_r', 'bgm_r',
  'fps', 'vol_orig', 'vol_hook_orig', 'vol_bgm', 'vol_voice', 'subtitle_y_percent',
  'subtitle_font_size_percent', 'concurrent_tasks', 'variant_seed',
  'subtitle_max_chars', 'subtitle_min_chars',
  'smart_select_min_score', 'smart_select_hook_top',
  'clip_duration_min', 'clip_duration_max', 'clip_duration_step',
] as const

function numberIfValid(value: unknown) {
  if (typeof value === 'number' || value === null) return value
  if (typeof value !== 'string' || !value.trim()) return value
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : value
}

export function getRandomResolutionOptions(resolution: string, minimum: string | number, maximum: string | number) {
  const match = String(resolution).trim().match(/^(\d+)\s*[*xX×]\s*(\d+)$/)
  if (!match) return null
  const width = Number(match[1]), height = Number(match[2])
  const min = Number(minimum), max = Number(maximum)
  if (![width, height].every(value => Number.isSafeInteger(value) && value > 0)
    || ![min, max].every(value => Number.isInteger(value) && value >= 128 && value <= 7680) || min > max) return null
  let a = width, b = height
  while (b) { const remainder = a % b; a = b; b = remainder }
  const widthRatio = width / a, heightRatio = height / a
  // A reduced ratio always has an odd side; an even multiplier makes both dimensions even.
  const step = 2 * Math.min(widthRatio, heightRatio)
  const first = Math.ceil(min / step) * step, last = Math.floor(max / step) * step
  const maxMultiplier = last / Math.min(widthRatio, heightRatio)
  return { step, first, last, count: Math.max(0, Math.floor((last - first) / step) + 1), aspectRatio: `${widthRatio}:${heightRatio}`,
    maxWidth: widthRatio * maxMultiplier, maxHeight: heightRatio * maxMultiplier }
}

export function validateRandomOutputConfig(config: VideoConfig): string | null {
  if (config.random_resolution_enabled) {
    const min = Number(config.random_resolution_min), max = Number(config.random_resolution_max)
    if (![min, max].every(value => Number.isInteger(value) && value >= 128 && value <= 7680)) {
      return '随机分辨率：短边范围须为 128–7680 的整数（px）'
    }
    if (min > max) return '随机分辨率：短边下限不能大于上限'
    const options = getRandomResolutionOptions(config.resolution, min, max)
    if (!options) return '随机分辨率：请填写有效的比例依据分辨率，例如 1080*1920'
    if (!options.count) return '随机分辨率：范围内没有保持当前宽高比且宽高均为偶数的尺寸，请扩大范围'
    if (Math.max(options.maxWidth, options.maxHeight) > 8192 || options.maxWidth * options.maxHeight > 8192 * 4320) {
      return '随机分辨率范围上限超过尺寸预算（长边≤8192，总像素≤35389440），请降低短边上限或调整比例。'
    }
  }
  if (config.random_bitrate_enabled) {
    const min = Number(config.random_bitrate_min), max = Number(config.random_bitrate_max)
    if (![min, max].every(value => Number.isInteger(value) && value >= 100 && value <= 200000)) {
      return '随机码率：范围须为 100–200000 的整数（kbps）'
    }
    if (min > max) return '随机码率：下限不能大于上限'
  }
  return null
}

export function normalizeConfigForRequest(config: VideoConfig): VideoConfig {
  const normalized = { ...config } as VideoConfig
  NUMERIC_CONFIG_FIELDS.forEach((key) => {
    ;(normalized as any)[key] = numberIfValid(config[key])
  })
  normalized.random_resolution_enabled = Boolean(config.random_resolution_enabled)
  normalized.random_resolution_min = normalized.random_resolution_enabled ? numberIfValid(config.random_resolution_min ?? 1080) as string | number : 1080
  normalized.random_resolution_max = normalized.random_resolution_enabled ? numberIfValid(config.random_resolution_max ?? 1440) as string | number : 1440
  normalized.random_bitrate_enabled = Boolean(config.random_bitrate_enabled)
  normalized.random_bitrate_min = normalized.random_bitrate_enabled ? numberIfValid(config.random_bitrate_min ?? 8000) as string | number : 8000
  normalized.random_bitrate_max = normalized.random_bitrate_enabled ? numberIfValid(config.random_bitrate_max ?? 14000) as string | number : 14000
  const randomOutputError = validateRandomOutputConfig(normalized)
  if (randomOutputError) throw new Error(randomOutputError)
  normalized.body_mode = config.body_mode === 'grouped' ? 'grouped' : 'normal'
  for (const scope of ['hook', 'body'] as const) {
    const key = `${scope}_zoom_percent` as const
    const percent = config[key] == null ? 100 : Number(config[key])
    if (!Number.isFinite(percent) || percent < 100 || percent > 300) throw new Error(`${scope === 'hook' ? 'Hook' : 'Body'} 放大比例须为 100–300%`)
    normalized[key] = percent
  }
  if ((config.body_groups || []).length > 10) throw new Error('Body 分组最多 10 组')
  const normalizedGroups = (config.body_groups || []).slice(0, 10).map((group) => {
    const safeGroup = group || {} as BodyGroup
    const clipCount = Number(safeGroup.clip_count)
    const clipDuration = Number(safeGroup.clip_duration)
    return {
      enabled: Boolean(safeGroup.enabled),
      folder: String(safeGroup.folder || '').trim(),
      full_duration: Boolean(safeGroup.full_duration),
      // Disabled rows stay at their position but must still satisfy Pydantic.
      clip_count: Number.isInteger(clipCount) && clipCount >= 1 ? clipCount : 1,
      clip_duration: Number.isFinite(clipDuration) && clipDuration >= 0.5 ? clipDuration : 3,
    }
  })
  normalized.body_groups = normalized.body_mode === 'grouped' ? normalizedGroups : []
  if (normalized.body_mode === 'grouped') {
    const groupClipCount = normalizedGroups.filter((group) => group.enabled)
      .reduce((total, group) => total + group.clip_count, 0)
    // These normal-mode inputs are hidden in grouped mode, so never submit stale invalid values.
    normalized.t_body = 3
    normalized.total_clips = Math.max(2, 1 + groupClipCount)
  }
  normalized.clip_duration_random_enabled = Boolean(config.clip_duration_random_enabled)
  normalized.clip_duration_scope = config.clip_duration_scope === 'all' ? 'all' : 'body'
  if (normalized.clip_duration_random_enabled) {
    const lo = Number(normalized.clip_duration_min), hi = Number(normalized.clip_duration_max)
    if (![lo, hi].every(value => Number.isFinite(value) && value >= 0.5 && value <= 120)) {
      throw new Error('片段随机时长：最短/最长须为 0.5–120 秒')
    }
    if (lo > hi) throw new Error('片段随机时长：最短时长不能大于最长时长')
    // 固定时长输入此时被随机区间接管，保底成合法值，避免隐藏字段触发后端校验
    const safeSeconds = (value: unknown, fallback: number) => {
      const parsed = Number(value)
      return Number.isFinite(parsed) && parsed >= 0.5 ? parsed : fallback
    }
    normalized.t_body = safeSeconds(normalized.t_body, 3)
    if (normalized.clip_duration_scope === 'all') normalized.t_hook = safeSeconds(normalized.t_hook, 3)
  }
  normalized.enable_random_cover = Boolean(config.enable_random_cover)
  normalized.body_full_duration = Boolean(config.body_full_duration)
  if (normalized.body_full_duration) normalized.t_body = 3
  if (normalized.body_mode === 'grouped' ? normalizedGroups.filter(g => g.enabled).every(g => g.full_duration) : normalized.body_full_duration) normalized.body_r = 0
  normalized.hook_full_duration = Boolean(config.hook_full_duration)
  if (normalized.hook_full_duration) {
    normalized.t_hook = 3 // Hidden fixed-duration input must not invalidate this mode.
    normalized.hook_r = 1
  }
  normalized.random_cover_mode = config.random_cover_mode === 'insert' ? 'insert' : 'replace'
  normalized.variant_strength = ['mild', 'balanced', 'strong'].includes(config.variant_strength) ? config.variant_strength : 'balanced'
  normalized.variant_hook = Boolean(config.variant_hook)
  normalized.variant_body = Boolean(config.variant_body)
  normalized.variant_crop = config.variant_crop !== false
  normalized.variant_color = Boolean(config.variant_color)
  normalized.variant_mirror = Boolean(config.variant_mirror)
  // Frame mixing belongs to the legacy transformer and is intentionally disabled in V2.
  normalized.variant_frame_mix = false
  normalized.variant_blend_enabled = Boolean(config.enable_variants && config.variant_blend_enabled)
  // Ignore stale paths when the optional blend is off so they cannot invalidate a task.
  normalized.variant_blend_path = normalized.variant_blend_enabled ? String(config.variant_blend_path || '').trim() : ''
  const blendOpacity = config.variant_blend_opacity == null ? Number.NaN : Number(config.variant_blend_opacity)
  normalized.variant_blend_opacity = Number.isFinite(blendOpacity)
    ? Math.max(0.01, Math.min(0.15, blendOpacity))
    : 0.03
  normalized.variant_blend_eof = ['loop', 'freeze', 'error'].includes(config.variant_blend_eof) ? config.variant_blend_eof : 'loop'
  if (config.bgm_tracks) {
    normalized.bgm_tracks = Object.fromEntries(Object.entries(config.bgm_tracks).map(([scope, track]) => {
      const inactive = !track.enabled || !track.path.trim()
      return [scope, { ...track, path: track.path.trim(),
        volume: inactive ? 30 : numberIfValid(track.volume),
        fade_in: inactive ? 0 : numberIfValid(track.fade_in),
        fade_out: inactive ? 0 : numberIfValid(track.fade_out),
        overlap: inactive || (scope === 'full' && config.duration_mode === 'bgm') || track.source_mode !== 'random' ? .3 : numberIfValid(track.overlap),
      }]
    })) as BgmTracks
    const full = normalized.bgm_tracks.full
    normalized.bgm_dir = full.enabled ? full.path : ''
    normalized.apply_bgm_to_hook = true
  }
  return normalized
}

export interface TaskStatus {
  task_id: string
  task_name: string
  status: 'pending' | 'running' | 'completed' | 'partial' | 'failed' | 'stopped'
  progress: number
  current: number
  total: number
  message: string
  log_lines: string[]
  created_at: string
  updated_at?: string
  output_files: string[]
  output_elapsed?: Record<string, number>
  output_warnings?: Record<string, string>
  acceleration?: string
  acceleration_warning?: string
  effective_concurrency?: number
  stopping?: boolean
  can_resume?: boolean
  resume_remaining?: number
}

export interface FramingPreview {
  image_url: string
  file_path: string
  time_seconds: number
}

export type ScheduleTrigger = 'once' | 'daily' | 'weekly' | 'interval'

export interface ScheduleItem {
  id: string
  name: string
  trigger_type: ScheduleTrigger
  time_of_day: string
  weekdays: number[]
  run_at: string
  interval_minutes: number
  enabled: boolean
  grace_minutes: number
  next_run: string
  last_run: string
  last_task_id: string
  last_result: '' | 'started' | 'busy' | 'missed' | 'error'
  last_message: string
  run_count: number
  created_at: string
  config?: VideoConfig
}

export interface SchedulePayload {
  name: string
  config: VideoConfig
  trigger_type: ScheduleTrigger
  time_of_day?: string
  weekdays?: number[]
  run_at?: string
  interval_minutes?: number
  grace_minutes?: number
}

export interface LlmSettingsInfo {
  base_url: string
  model: string
  vision_model: string
  temperature: number
  has_key: boolean
}

export type ShotLevel = 'hook' | 'body' | 'low'

export interface AnalysisShot {
  index: number
  start: number
  end: number
  duration: number
  blur: number | null
  yavg: number | null
  motion: number | null
  score: number
  level: ShotLevel
  tags: string[]
  thumb: string
  parts?: Record<string, number>
  vlm?: {
    description: string
    tags: string[]
    onscreen_text: string
    has_person: boolean
    product_visible: boolean
    hook_score: number
    usage: 'hook' | 'body' | 'unusable'
  } | null
}

export interface AnalysisResult {
  file: string
  name: string
  duration: number
  width: number
  height: number
  fps: number
  has_audio: boolean
  vlm_used: boolean
  vlm_errors: number
  shots: AnalysisShot[]
  summary: {
    shot_count: number
    avg_score: number
    hook_count: number
    body_count: number
    low_count: number
    best_index: number | null
    best_score: number
  }
}

export interface AnalysisFileEntry {
  name: string
  path: string
  key: string
  duration: number
  shot_count: number
  avg_score: number
  best_score: number
  hook_count: number
  vlm_used: boolean
  vlm_errors: number
  error: string
}

export interface AnalysisJob {
  id: string
  status: 'running' | 'stopping' | 'completed' | 'stopped' | 'failed'
  directory: string
  total: number
  done: number
  stage: string
  stage_done: number
  stage_total: number
  current: string
  files: AnalysisFileEntry[]
  errors: { name: string; error: string }[]
  use_vlm: boolean
  started_at: string
  finished_at: string
}

export const getApiBase = getBaseUrl

export const api = {
  framingPreview: (paths: string[], signal?: AbortSignal) => request<FramingPreview>('/framing/preview', {
    method: 'POST', body: JSON.stringify({ paths }), signal,
  }),
  health: () => request<{ status: string }>('/health'),

  createTask: (config: VideoConfig) =>
    request<{ task_id: string; message: string }>('/tasks', {
      method: 'POST',
      body: JSON.stringify({ config: normalizeConfigForRequest(config) }),
    }),

  listTasks: () => request<TaskStatus[]>('/tasks'),

  getTask: (taskId: string) => request<TaskStatus>(`/tasks/${taskId}`),

  stopTask: (taskId: string) =>
    request<{ message: string }>(`/tasks/${taskId}/stop`, { method: 'POST' }),

  resumeTask: (taskId: string) =>
    request<{ task_id: string; message: string }>(`/tasks/${taskId}/resume`, { method: 'POST' }),

  getLogs: (taskId: string) => request<{ logs: string[] }>(`/tasks/${taskId}/logs`),

  streamLogs: async (taskId: string, onLog: (data: any) => void) => {
    const baseUrl = await getBaseUrl()
    const eventSource = new EventSource(`${baseUrl}/tasks/${taskId}/stream`)
    eventSource.onmessage = (e) => {
      if (e.data === '[DONE]') {
        eventSource.close()
        return
      }
      try {
        onLog(JSON.parse(e.data))
      } catch {
        onLog({ log: e.data })
      }
    }
    eventSource.onerror = () => eventSource.close()
    return () => eventSource.close()
  },

  scanDirectory: (dirPath: string, extensions: string[] = ['.mp4', '.mov']) =>
    request<{ files: string[]; count: number }>('/scan', {
      method: 'POST',
      body: JSON.stringify({ dir_path: dirPath, extensions }),
    }),

  probeFile: (filePath: string) =>
    request<any>(`/probe?file_path=${encodeURIComponent(filePath)}`, { method: 'POST' }),

  benchmark: (config: VideoConfig) =>
    request<any>('/benchmark', {
      method: 'POST',
      body: JSON.stringify(normalizeConfigForRequest(config)),
    }),

  preflight: (config: VideoConfig) =>
    request<any>('/preflight', {
      method: 'POST',
      body: JSON.stringify(normalizeConfigForRequest(config)),
    }),

  clearHistory: () =>
    request<{ message: string }>('/history/clear', { method: 'POST' }),

  listSchedules: () => request<ScheduleItem[]>('/schedules'),

  createSchedule: (payload: SchedulePayload) =>
    request<ScheduleItem>('/schedules', {
      method: 'POST',
      body: JSON.stringify({ ...payload, config: normalizeConfigForRequest(payload.config) }),
    }),

  updateSchedule: (id: string, patch: Partial<Omit<SchedulePayload, 'config'>> & { enabled?: boolean }) =>
    request<ScheduleItem>(`/schedules/${id}`, {
      method: 'PATCH',
      body: JSON.stringify(patch),
    }),

  deleteSchedule: (id: string) =>
    request<{ message: string }>(`/schedules/${id}`, { method: 'DELETE' }),

  runScheduleNow: (id: string) =>
    request<{ task_id: string; message: string }>(`/schedules/${id}/run-now`, { method: 'POST' }),

  getLlmSettings: () => request<LlmSettingsInfo>('/llm/settings'),

  saveLlmSettings: (payload: {
    base_url?: string; api_key?: string; model?: string;
    temperature?: number; clear_key?: boolean
  }) =>
    request<LlmSettingsInfo>('/llm/settings', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  generateCopy: (payload: { brief: string; count: number; style: string; duration: number }) =>
    request<{ items: string[]; model: string }>('/llm/generate', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  copyToSrt: (payload: { text: string; directory: string; filename?: string; chars_per_second?: number }) =>
    request<{ path: string; cues: number; duration: number }>('/llm/to-srt', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  startAnalysis: (payload: {
    directory: string; recursive?: boolean; use_vlm?: boolean;
    limit?: number; max_shots?: number;
  }) =>
    request<AnalysisJob>('/analyze', { method: 'POST', body: JSON.stringify(payload) }),

  getAnalysisJob: (id: string) => request<AnalysisJob>(`/analyze/${id}`),

  stopAnalysis: (id: string) =>
    request<{ message: string }>(`/analyze/${id}/stop`, { method: 'POST' }),

  latestAnalysisJob: () => request<AnalysisJob | Record<string, never>>('/analyze/latest'),

  getAnalysisResult: (key: string) => request<AnalysisResult>(`/analysis/${key}`),

  clearAnalysis: () => request<{ removed: number }>('/analysis/clear', { method: 'POST' }),
}
