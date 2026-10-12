import { useEffect, useId, useLayoutEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Slider } from './ui/slider'
import { api, FramingPreview } from '../api/client'

export function SourceFramingSettings({ scope, value, onChange, paths, resolution }: {
  scope: 'Hook' | 'Body'; value: string | number; onChange: (percent: number) => void
  paths: string[]; resolution: string
}) {
  const dialog = useRef<HTMLDialogElement>(null)
  const trigger = useRef<HTMLButtonElement>(null)
  const id = useId()
  const [draft, setDraft] = useState(String(value))
  const [invalid, setInvalid] = useState(false)
  const [preview, setPreview] = useState<FramingPreview | null>(null)
  const [loading, setLoading] = useState(false)
  const [previewError, setPreviewError] = useState('')
  const controller = useRef<AbortController | null>(null)
  const sequence = useRef(0)
  const sourceKey = JSON.stringify(paths.filter(path => path.trim()))
  const dimensions = resolution.match(/^(\d+)\s*[*xX×]\s*(\d+)$/)
  const aspect = dimensions && Number(dimensions[1]) > 0 && Number(dimensions[2]) > 0
    ? Number(dimensions[1]) / Number(dimensions[2]) : 9 / 16
  const previewWidth = Math.min(258, 230 * aspect)
  const percent = Number(value) || 100
  const kept = 100 / percent
  const margin = (1 - kept) / 2
  const commit = (next: number) => { onChange(next); setDraft(String(next)); setInvalid(false) }
  function position() {
    const el = dialog.current!
    if (!el.open) return
    const rect = trigger.current!.getBoundingClientRect()
    el.style.margin = '0'
    el.style.left = `${Math.max(12, Math.min(rect.left, window.innerWidth - el.offsetWidth - 12))}px`
    el.style.top = `${Math.max(12, Math.min(rect.bottom + 8, window.innerHeight - el.offsetHeight - 12))}px`
  }
  useLayoutEffect(position, [preview, loading, previewError, invalid, aspect])
  useEffect(() => {
    sequence.current++
    controller.current?.abort()
    setPreview(null); setLoading(false); setPreviewError('')
    return () => { sequence.current++; controller.current?.abort() }
  }, [sourceKey])
  function open() {
    setDraft(String(percent)); setInvalid(false)
    dialog.current!.showModal()
    position()
  }
  async function loadPreview() {
    const candidates: string[] = JSON.parse(sourceKey)
    if (!candidates.length) { setPreviewError(`请先选择 ${scope} 素材目录`); return }
    controller.current?.abort()
    const abort = new AbortController()
    controller.current = abort
    const ticket = ++sequence.current
    setLoading(true); setPreviewError('')
    try {
      const result = await api.framingPreview(candidates, abort.signal)
      if (!abort.signal.aborted && ticket === sequence.current) setPreview(result)
    } catch (error) {
      if (!abort.signal.aborted && ticket === sequence.current) setPreviewError(error instanceof Error ? error.message : '预览失败，请重试')
    } finally {
      if (ticket === sequence.current) setLoading(false)
    }
  }
  return <>
    <button ref={trigger} type="button" onClick={open} aria-label={`${scope}画面设置`} aria-haspopup="dialog"
      className={`h-7 shrink-0 cursor-pointer rounded-[4px] border px-2 text-[11px] transition-colors focus-visible:ring-2 focus-visible:ring-accent ${percent > 100 ? 'border-accent/50 text-accent' : 'border-border/[0.08] text-foreground/85 hover:border-accent/50 hover:text-accent'}`}>
      设置{percent > 100 ? ` ${percent}%` : ''}
    </button>
    {createPortal(<dialog ref={dialog} className="feature-help-dialog" aria-labelledby={id}
      style={preview || loading ? { width: 'min(600px, calc(100vw - 24px))' } : undefined}
      onClose={() => { sequence.current++; controller.current?.abort(); setLoading(false); trigger.current?.focus() }}
      onClick={event => {
        if (event.target !== event.currentTarget) return
        const r = event.currentTarget.getBoundingClientRect()
        if (event.clientX < r.left || event.clientX > r.right || event.clientY < r.top || event.clientY > r.bottom) dialog.current?.close()
      }}>
      <div className="help-heading"><h2 id={id}>{scope} 素材放大裁切</h2><button type="button" aria-label={`关闭${scope}设置`} onClick={() => dialog.current?.close()}>关闭</button></div>
      <p className="text-xs text-foreground/80">{scope === 'Body' ? '应用于普通 Body 和全部分组。' : '应用于所有 Hook，包括成品 Hook。'}等比放大，居中裁掉四边，输出比例不变。</p>
      <div className="my-3 flex items-center gap-3">
        <label htmlFor={`${id}-percent`} className="shrink-0 text-xs">放大比例</label>
        <input id={`${id}-percent`} aria-label={`${scope}放大比例百分比`} aria-invalid={invalid} type="number" min="100" max="300" step="1" value={draft}
          className="h-8 w-20 rounded border border-border/30 bg-background-elev px-2 text-sm text-foreground focus-visible:ring-2 focus-visible:ring-accent"
          onChange={event => {
            setDraft(event.target.value)
            const next = Number(event.target.value)
            if (Number.isFinite(next) && next >= 100 && next <= 300) { onChange(next); setInvalid(false) }
          }} onBlur={() => {
            const next = Number(draft)
            if (Number.isFinite(next) && next >= 100 && next <= 300) commit(next)
            else setInvalid(true)
          }} />
        <span className="text-xs">%</span>
      </div>
      {invalid && <p role="alert" className="mb-2 text-xs text-hot">请输入 100–300%；当前有效设置仍为 {percent}%。</p>}
      <Slider value={[percent]} min={100} max={300} step={1} onValueChange={([next]) => commit(next)} ariaLabel={`${scope}放大比例`} />
      <div className="mt-3 flex items-center gap-3">
        <button type="button" onClick={loadPreview} disabled={loading}
          aria-label={`${scope}${preview ? '换一帧' : '预览'}`}
          className="h-8 rounded border border-accent/50 px-3 text-xs text-accent hover:bg-accent/10 disabled:cursor-wait disabled:opacity-50 focus-visible:ring-2 focus-visible:ring-accent">
          {loading ? '随机取帧中…' : preview ? '换一帧' : '预览'}
        </button>
        <span className="text-xs text-muted-foreground">点击才取帧；调比例即时预览</span>
      </div>
      {loading && <p role="status" className="my-2 text-xs text-muted-foreground">正在读取素材，请稍候…</p>}
      {previewError && <p role="alert" className="my-2 text-xs text-hot">{previewError}</p>}
      {preview ? <div className="mt-3" aria-label={`${scope}真实素材预览`}>
        <div className="flex items-start justify-center gap-3">
          {[100, percent].map((zoom, index) => <figure key={index} className="min-w-0 flex-1">
            <figcaption className="mb-1 text-center text-xs text-foreground/80">{index === 0 ? '原画幅 100%' : `当前 ${percent}%`}</figcaption>
            <div className="relative mx-auto max-w-full overflow-hidden rounded border border-border/30 bg-black"
              style={{ width: previewWidth, aspectRatio: String(aspect) }}>
              <img src={preview.image_url} alt={`${scope}${index === 0 ? '原画幅' : '放大裁切'}预览`}
                className="absolute inset-0 h-full w-full object-cover" style={{ transform: `scale(${zoom / 100})`, transformOrigin: 'center' }} />
            </div>
          </figure>)}
        </div>
        <p className="mt-2 truncate text-xs text-muted-foreground" title={preview.file_path}>{preview.file_path.split(/[\\/]/).pop()} · {preview.time_seconds.toFixed(2)} 秒</p>
        <p className="mt-1 text-xs text-muted-foreground">按当前输出比例预览，不含软件新增字幕、水印或变换；仅代表这条素材。</p>
      </div> : <svg className="my-3 h-20 w-full text-accent" viewBox="0 0 200 92" role="img" aria-label="示意图：保留画面中心，裁去四周">
        <rect x="10" y="6" width="180" height="80" rx="3" fill="currentColor" fillOpacity=".08" stroke="currentColor" strokeOpacity=".35" />
        <rect x={10 + 180 * margin} y={6 + 80 * margin} width={180 * kept} height={80 * kept} fill="currentColor" fillOpacity=".12" stroke="currentColor" />
        <text x="100" y="50" textAnchor="middle" fill="currentColor" fontSize="12">保留中心</text>
      </svg>}
      <p className="mt-2 text-xs text-foreground/80">100% 不额外裁切；当前宽高保留约 {(kept * 100).toFixed(1)}%，四边各额外裁掉约 {(margin * 100).toFixed(1)}%。{!preview && '点击“预览”查看素材实际画面。'}</p>
      <p className="mt-2 text-xs text-foreground/80">原片边缘的字幕和 Logo 也可能被裁掉；软件后加的字幕、水印不受影响。仅影响画面，不改变音频或时长。</p>
      <div className="mt-3 flex items-center justify-between gap-2 text-xs">
        <span className="text-muted-foreground">设置自动保存 · 100–300%</span>
        <button type="button" onClick={() => commit(100)} className="rounded border border-border/30 px-2 py-1 hover:text-accent focus-visible:ring-2 focus-visible:ring-accent">恢复 100%</button>
      </div>
    </dialog>, document.body)}
  </>
}
