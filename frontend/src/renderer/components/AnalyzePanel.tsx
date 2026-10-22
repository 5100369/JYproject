import { useEffect, useState } from 'react'
import { api, AnalysisJob, AnalysisResult, getApiBase } from '../api/client'
import { Checkbox } from './ui/checkbox'
import { useStore } from '../store'

function scoreColor(score: number): string {
  if (score >= 70) return 'text-ok'
  if (score >= 40) return 'text-accent'
  return 'text-hot'
}

function scoreBorder(score: number): string {
  if (score >= 70) return 'border-ok/70'
  if (score >= 40) return 'border-accent/70'
  return 'border-hot/60'
}

export default function AnalyzePanel({ pickDirectory }: {
  pickDirectory: () => Promise<string | undefined>
}) {
  const { config, setConfig, addToast } = useStore()
  const [base, setBase] = useState('')
  const [directory, setDirectory] = useState('')
  const [useVlm, setUseVlm] = useState(false)
  const [job, setJob] = useState<AnalysisJob | null>(null)
  const [starting, setStarting] = useState(false)
  const [expanded, setExpanded] = useState('')
  const [detail, setDetail] = useState<AnalysisResult | null>(null)
  const [loadingDetail, setLoadingDetail] = useState(false)

  useEffect(() => {
    getApiBase().then(setBase).catch(() => setBase(''))
    api.latestAnalysisJob()
      .then(result => {
        if (result && (result as AnalysisJob).id) {
          const latest = result as AnalysisJob
          setJob(latest)
          setDirectory(latest.directory || '')
        }
      })
      .catch(() => { /* 首次启动没有历史任务 */ })
  }, [])

  useEffect(() => {
    if (!job || !['running', 'stopping'].includes(job.status)) return
    const timer = setInterval(() => {
      api.getAnalysisJob(job.id).then(setJob).catch(() => { /* 忽略瞬时失败 */ })
    }, 1200)
    return () => clearInterval(timer)
  }, [job?.id, job?.status])

  const running = job?.status === 'running' || job?.status === 'stopping'

  const pick = async () => {
    const selected = await pickDirectory()
    if (selected) setDirectory(selected)
  }

  const start = async () => {
    if (!directory.trim()) {
      addToast('先选一个素材目录', 'warning')
      return
    }
    setStarting(true)
    try {
      const created = await api.startAnalysis({
        directory: directory.trim(),
        recursive: true,
        use_vlm: useVlm,
        max_shots: 40,
      })
      setJob(created)
      setExpanded('')
      setDetail(null)
      addToast(`开始分析 ${created.total} 个素材`, 'success')
    } catch (error: any) {
      addToast(error?.message || '分析启动失败', 'error')
    } finally {
      setStarting(false)
    }
  }

  const stop = async () => {
    if (!job) return
    try {
      await api.stopAnalysis(job.id)
      addToast('已请求停止', 'info')
    } catch (error: any) {
      addToast(error?.message || '停止失败', 'error')
    }
  }

  const clear = async () => {
    if (!window.confirm('清空所有分析结果？不会动你的素材，只是需要重新分析。')) return
    try {
      const result = await api.clearAnalysis()
      addToast(`已清空 ${result.removed} 份分析结果`, 'info')
      setJob(null)
      setDetail(null)
      setExpanded('')
    } catch (error: any) {
      addToast(error?.message || '清空失败', 'error')
    }
  }

  const openDetail = async (key: string) => {
    if (expanded === key) {
      setExpanded('')
      setDetail(null)
      return
    }
    setExpanded(key)
    setDetail(null)
    setLoadingDetail(true)
    try {
      setDetail(await api.getAnalysisResult(key))
    } catch (error: any) {
      addToast(error?.message || '读取分析结果失败', 'error')
    } finally {
      setLoadingDetail(false)
    }
  }

  const progress = job && job.total > 0
    ? Math.round((job.done / job.total) * 100)
    : 0

  return (
    <div role="region" aria-label="素材分析" className="absolute inset-0 overflow-y-auto bg-background/70 px-4 py-4">
      <div className="flex items-center justify-between border-b border-border/[0.06] pb-3">
        <div>
          <div className="text-[13px] font-semibold text-foreground">素材分析</div>
          <div className="mt-0.5 font-mono text-[10px] text-muted-foreground">SHOT ANALYSIS</div>
        </div>
        <button type="button" onClick={clear}
          className="text-[10px] text-muted-foreground hover:text-hot">清空结果</button>
      </div>

      {/* 目录与参数 */}
      <div className="mt-3 space-y-1.5">
        <div className="flex items-center gap-1.5">
          <input
            aria-label="分析目录"
            value={directory}
            onChange={event => setDirectory(event.target.value)}
            placeholder="素材目录"
            className="h-7 min-w-0 flex-1 rounded-[4px] border border-border/[0.10] bg-background-elev px-2 font-mono text-[10px] text-foreground outline-none placeholder:text-muted-foreground/50 focus:border-accent/70"
          />
          <button type="button" onClick={pick}
            className="h-7 shrink-0 rounded-[4px] border border-border/[0.10] px-2 text-[10px] text-foreground/85 hover:border-accent/60 hover:text-accent">
            选择
          </button>
        </div>

        <label className="flex cursor-pointer items-start gap-1.5">
          <Checkbox className="mt-0.5" checked={useVlm} onCheckedChange={value => setUseVlm(value === true)} />
          <span className="text-[10px] leading-4 text-foreground/85">
            用视觉模型理解画面
            <span className="ml-1 text-[9px] text-muted-foreground">
              需要先在「文案」页配好 Key 和视觉模型，会产生 API 费用
            </span>
          </span>
        </label>

        <div className="flex items-center gap-1.5">
          <button type="button" onClick={start} disabled={starting || running}
            className="h-8 flex-1 rounded-[4px] bg-accent text-[12px] font-semibold text-background hover:bg-accent-hover disabled:opacity-50">
            {starting ? '启动中…' : running ? '分析进行中' : '开始分析'}
          </button>
          {running && (
            <button type="button" onClick={stop}
              className="h-8 shrink-0 rounded-[4px] bg-hot px-3 text-[11px] font-semibold text-white hover:bg-hot/90">
              停止
            </button>
          )}
        </div>
      </div>

      {/* 进度 */}
      {job && (
        <div className="mt-3 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-2">
          <div className="flex items-center justify-between text-[10px]">
            <span className="text-foreground/85">
              {job.status === 'completed' ? '分析完成'
                : job.status === 'stopped' ? '已停止'
                : job.status === 'failed' ? '失败'
                : `正在分析 ${job.done}/${job.total}`}
            </span>
            <span className="font-mono text-accent">
              {job.stage}{job.stage_total ? ` ${job.stage_done}/${job.stage_total}` : ''}
            </span>
          </div>
          <div className="mt-1.5 h-1 overflow-hidden rounded-full bg-foreground/10">
            <div className="h-full rounded-full bg-accent transition-all"
              style={{ width: `${job.status === 'completed' ? 100 : progress}%` }} />
          </div>
          {job.current && running && (
            <div className="mt-1 truncate font-mono text-[9px] text-muted-foreground">{job.current}</div>
          )}
          {job.errors.length > 0 && (
            <div className="mt-1 text-[9px] text-hot">跳过 {job.errors.length} 个：{job.errors[0].name}</div>
          )}
        </div>
      )}

      {/* 结果 */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">分析结果</span>
          <span className="text-[10px] text-muted-foreground">{job?.files.length || 0} 个素材</span>
        </div>

        {!job || job.files.length === 0 ? (
          <div className="rounded-[5px] border border-dashed border-border/[0.10] px-3 py-6 text-center text-[11px] text-muted-foreground/70">
            选好素材目录点开始分析。会按镜头切开、逐段打分，标出适合当开头的高光片段。
          </div>
        ) : (
          <div className="space-y-1.5">
            {job.files.map(entry => (
              <div key={`${entry.name}-${entry.key}`}>
                <button
                  type="button"
                  onClick={() => entry.key && openDetail(entry.key)}
                  disabled={!entry.key}
                  className="w-full rounded-[5px] border border-border/[0.10] bg-foreground/[0.025] px-2.5 py-1.5 text-left transition-colors hover:border-accent/40 disabled:opacity-60"
                >
                  <div className="flex items-center justify-between gap-2">
                    <span className="truncate text-[11px] text-foreground">{entry.name}</span>
                    <span className={`shrink-0 font-mono text-[11px] ${scoreColor(entry.avg_score)}`}>
                      {entry.error ? '失败' : entry.avg_score}
                    </span>
                  </div>
                  <div className="mt-0.5 flex items-center gap-2 font-mono text-[9px] text-muted-foreground">
                    <span>{entry.duration.toFixed(1)}s</span>
                    <span>{entry.shot_count} 镜</span>
                    <span>高光 {entry.hook_count}</span>
                    {entry.vlm_used && <span className="text-accent">已语义</span>}
                  </div>
                </button>

                {expanded === entry.key && (
                  <div className="mt-1 rounded-[5px] border border-border/[0.08] bg-background/60 px-2 py-2">
                    {loadingDetail && <div className="py-3 text-center text-[10px] text-muted-foreground">读取中…</div>}
                    {detail && detail.shots && (
                      <>
                        <div className="grid grid-cols-3 gap-1.5">
                          {detail.shots.map(shot => (
                            <div key={shot.index}
                              title={`${shot.start.toFixed(1)}-${shot.end.toFixed(1)}s · ${shot.score} 分\n${shot.tags.join(' / ') || '无标签'}${shot.vlm?.description ? `\n${shot.vlm.description}` : ''}${shot.vlm?.onscreen_text ? `\n文字：${shot.vlm.onscreen_text}` : ''}`}
                              className={`overflow-hidden rounded-[3px] border ${scoreBorder(shot.score)}`}>
                              {base && shot.thumb ? (
                                <img src={`${base}/analysis/thumb/${entry.key}/${shot.thumb}`}
                                  alt={`镜头 ${shot.index + 1}`} loading="lazy"
                                  className="aspect-video w-full object-cover" />
                              ) : (
                                <div className="aspect-video w-full bg-foreground/[0.06]" />
                              )}
                              <div className="flex items-center justify-between px-1 py-0.5 font-mono text-[8px]">
                                <span className="text-muted-foreground">{shot.start.toFixed(1)}s</span>
                                <span className={scoreColor(shot.score)}>{shot.score}</span>
                              </div>
                            </div>
                          ))}
                        </div>
                        <p className="mt-1.5 text-[9px] leading-4 text-muted-foreground">
                          共 {detail.summary.shot_count} 镜，均分 {detail.summary.avg_score}。
                          绿色可直接做开头，橙色适合做 Body，红色建议不用。
                        </p>
                      </>
                    )}
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>

      {/* 选片开关 */}
      <div className="mt-4 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-2.5">
        <label className="flex cursor-pointer items-center justify-between gap-2">
          <span className="text-[11px] text-foreground/85">混剪时按分析结果选片</span>
          <Checkbox
            checked={config.smart_select_enabled === true}
            onCheckedChange={value => setConfig({ smart_select_enabled: value === true })}
          />
        </label>
        <div className="mt-1.5 flex items-center gap-1.5">
          <span className="text-[9px] text-muted-foreground">最低分</span>
          <input
            aria-label="智能选片最低分"
            type="number" min={0} max={100}
            value={String(config.smart_select_min_score ?? 0)}
            onChange={event => setConfig({ smart_select_min_score: Number(event.target.value) || 0 })}
            className="h-6 w-[58px] rounded-[4px] border border-border/[0.10] bg-background-elev px-1.5 font-mono text-[10px] text-foreground outline-none focus:border-accent/70"
          />
          <span className="text-[9px] text-muted-foreground">分以下不用</span>
        </div>
        <p className="mt-1 text-[9px] leading-4 text-muted-foreground">
          只对分析过的素材生效；没分析过的会自动回退成原来的等间隔切片，不会出不了片。
        </p>
      </div>
    </div>
  )
}
