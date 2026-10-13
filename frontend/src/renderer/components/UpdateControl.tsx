import { useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../store'
import type { UpdatePreferences, UpdateState, UpdateSource, UpdateSourceCheck, UpdateTestScenario } from '../../shared/updates'

const PHASES: Record<UpdateState['phase'], string> = {
  idle: '在线更新', checking: '检查中', current: '已是最新版', available: '发现新版', waiting: '等待空闲',
  downloading: '下载中', paused: '下载暂停', verifying: '校验中', ready: '更新已就绪', installing: '确认安装', error: '更新暂不可用',
}
const size = (bytes: number) => `${(bytes / 1024 ** 2).toFixed(1)} MB`
export default function UpdateControl() {
  const dialog = useRef<HTMLDialogElement>(null)
  const [state, setState] = useState<UpdateState | null>(null)
  const [draft, setDraft] = useState<UpdatePreferences | null>(null)
  const [error, setError] = useState('')
  const [request, setRequest] = useState(false)
  const [presets, setPresets] = useState<UpdateSource[]>([])
  const [checks, setChecks] = useState<UpdateSourceCheck[]>([])
  const [scenario, setScenario] = useState<UpdateTestScenario>('normal')
  const tasksBusy = useStore(store => store.tasks.some(task => task.status === 'running' || task.status === 'pending' || task.stopping))
  const preferenceKey = JSON.stringify(state?.preferences ?? null)
  useEffect(() => {
    const api = window.electronAPI
    if (!api?.getUpdateState || !api.onUpdateState) { setError('当前开发版需重启，才能加载新的更新器。'); return }
    let cancelled = false
    const unsubscribe = api.onUpdateState(value => { if (!cancelled) setState(value) })
    void api.getUpdateState().then(value => { if (!cancelled && value) setState(value) }).catch(reason => { if (!cancelled) setError(String(reason)) })
    if (api.getUpdatePresets) void api.getUpdatePresets().then(value => { if (!cancelled) setPresets(value) }).catch(() => {})
    return () => { cancelled = true; unsubscribe() }
  }, [])
  useEffect(() => { setDraft(preferenceKey === 'null' ? null : JSON.parse(preferenceKey)) }, [preferenceKey])
  const locked = request || !!state && ['checking', 'downloading', 'verifying', 'installing'].includes(state.phase)
  const run = async (action: () => Promise<unknown>) => {
    if (request) return
    setRequest(true); setError('')
    try { await action() } catch (reason) { setError(String(reason).replace(/^Error: /, '')) }
    finally { setRequest(false) }
  }
  const ready = state?.phase === 'ready'
  const percent = state?.total ? Math.min(100, Math.round(state.received / state.total * 100)) : 0
  const resultFor = (source: UpdateSource) => checks.find(result => result.id === source.id && result.kind === source.kind && result.url === source.url)
  const missingPresets = presets.filter(preset => !draft?.sources.some(source => source.kind === preset.kind && source.url === preset.url))
  return <>
    <button type="button" className="tutorial-trigger update-trigger" onClick={() => {
      setDraft(state ? structuredClone(state.preferences) : null); setError(''); dialog.current?.showModal()
      if (!window.electronAPI?.getUpdateState) setError('当前开发版需重启，才能加载新的更新器。')
    }} aria-label="在线更新设置" title="检查新版、下载进度与下载源设置">
      {ready ? '更新已就绪' : state?.version ? `更新 ${state.version}` : '在线更新'}
      {state?.phase === 'downloading' && <span className="ml-1 font-mono">{percent}%</span>}
    </button>
    {createPortal(<dialog ref={dialog} className="feature-help-dialog update-dialog" aria-labelledby="update-dialog-title" onClick={event => {
      if (event.target === dialog.current) { const bounds = dialog.current.getBoundingClientRect(); if (event.clientX < bounds.left || event.clientX > bounds.right || event.clientY < bounds.top || event.clientY > bounds.bottom) dialog.current.close() }
    }}>
      <div className="help-heading"><h2 id="update-dialog-title">在线更新</h2><button type="button" onClick={() => dialog.current?.close()} aria-label="关闭在线更新">关闭</button></div>
      <div className="update-summary">
        <div className="flex justify-between gap-3"><span>当前版本 <span className="font-mono">{state?.currentVersion ?? '—'}</span>{state?.development && ' · 开发版'}{state?.testMode && ' · 演练中'}</span><span className="text-accent">{state ? PHASES[state.phase] : '等待更新器'}</span></div>
        {state?.version && <p>目标版本 <span className="font-mono">{state.version}</span>{state.source && ` · ${state.source}`}</p>}
        <p className="update-message" role="status" aria-live="polite">{state?.message ?? '初始化后可检查新版。'}</p>
        {!!state?.total && <><progress className="update-progress" value={state.received} max={state.total} aria-label="安装包下载进度" /><p className="font-mono text-xs">{size(state.received)} / {size(state.total)} · {percent}%</p></>}
      </div>
      {error && <p className="text-hot whitespace-pre-wrap" role="alert">{error}</p>}
      <div className="update-actions">
        <button type="button" disabled={!state || locked} onClick={() => void run(() => window.electronAPI.checkUpdates())}>检查更新</button>
        {state?.version && !ready && <button type="button" disabled={locked} onClick={() => void run(() => window.electronAPI.downloadUpdate())}>下载 / 继续</button>}
        {state && ['checking', 'waiting', 'downloading', 'verifying'].includes(state.phase) && <button type="button" onClick={() => void window.electronAPI.pauseUpdate().catch(reason => setError(String(reason)))}>暂停</button>}
        {ready && !state.testMode && <><button type="button" className="update-primary" disabled={locked || state.development || tasksBusy || state.busy} onClick={() => void run(() => window.electronAPI.installUpdate())}>{state.platform.startsWith('darwin') ? '打开 DMG 安装' : '确认安装更新'}</button>
          <button type="button" disabled={locked} onClick={() => void run(() => window.electronAPI.revealUpdate())}>安装包目录</button></>}
      </div>
      <p className="update-hint">混剪 / 压测期间暂停下载，停止清理结束后才可安装。重启不能恢复旧任务的续跑记录，已有成片保留。开发版只检查、下载，不执行安装。</p>
      {state?.development && <div className="update-summary">
        <strong>更新演练 · 不安装、不改原配置</strong>
        <p className="update-hint">进入演练 → 检查更新 → 下载 / 继续。下载时可暂停再继续；999.0.0 是虚拟版本，1 MB 为本地测试数据，不代表真实网络速度。</p>
        <div className="update-actions">
          <label>演练场景 <select className="update-select" aria-label="演练场景" value={scenario} disabled={locked || !!state.testMode} onChange={event => setScenario(event.target.value as UpdateTestScenario)}>
            <option value="normal">正常下载 / 暂停续传</option><option value="fallback">主源失败，备用源回退</option>
            <option value="signature">清单签名错误，应阻止下载</option><option value="hash">文件校验错误，应阻止安装</option>
          </select></label>
          <button type="button" disabled={locked} onClick={() => void run(async () => {
            if (!window.electronAPI.setUpdateDemo) throw new Error('需重启测试版才能加载演练功能')
            setState(await window.electronAPI.setUpdateDemo(state.testMode ? null : scenario))
          })}>{state.testMode ? '退出演练' : '进入更新演练'}</button>
        </div>
      </div>}
      {state?.notes.length ? <details className="update-notes" open><summary>本版更新内容</summary><div>{state.notes.map((note, index) => <p key={index}>{note}</p>)}</div></details> : null}
      {draft && !state?.testMode && <details className="update-sources"><summary>下载源与自动更新设置</summary>
        <div className="update-actions">
          <button type="button" disabled={locked || tasksBusy} onClick={() => void run(async () => {
            if (!window.electronAPI.probeUpdateSources) throw new Error('需重启测试版才能检测下载源')
            setChecks([]); setChecks(await window.electronAPI.probeUpdateSources(draft))
          })}>{request ? '操作中…' : '检测全部源'}</button>
          <button type="button" disabled={locked || !checks.some(result => result.reachable)} onClick={() => setDraft({ ...draft, sources: [...draft.sources].sort((a, b) => {
            const left = resultFor(a), right = resultFor(b)
            return (left?.reachable ? left.elapsedMs : Infinity) - (right?.reachable ? right.elapsedMs : Infinity)
          }) })}>按响应时间排序</button>
          <button type="button" disabled={locked || !missingPresets.length || draft.sources.length >= 8} onClick={() => {
            const sources = [...draft.sources]
            for (const preset of missingPresets) {
              if (sources.length === 8) break
              let id = preset.id
              let sequence = 0
              while (sources.some(source => source.id === id)) id = `preset-${Date.now()}-${sequence++}`
              sources.push({ ...preset, id })
            }
            setDraft({ ...draft, sources }); setChecks([])
          }}>补充预设加速源</button>
        </div>
        <p className="update-hint">手动检测当前填写的地址（无需先保存）。响应时间包含 DNS、TLS 与 HTTP 首字节，不是 ping，也不代表大文件速度；只读取少量数据，不下载完整安装包。排序后点“保存设置”生效。</p>
        <div className="update-options">
          <label><input type="checkbox" checked={draft.autoCheck} disabled={locked} onChange={event => setDraft({ ...draft, autoCheck: event.target.checked })} />启动自动检查（下次启动生效）</label>
          <label><input type="checkbox" checked={draft.autoDownload} disabled={locked} onChange={event => setDraft({ ...draft, autoDownload: event.target.checked })} />发现新版后空闲自动下载</label>
        </div>
        {draft.sources.map((source, index) => <div className="update-source" key={source.id}>
          <label><input type="checkbox" checked={source.enabled} disabled={locked} onChange={event => setDraft({ ...draft, sources: draft.sources.map((item, i) => i === index ? { ...item, enabled: event.target.checked } : item) })} />{source.label}</label>
          {source.kind !== 'github' && <label>HTTPS {source.kind === 'proxy' ? '加速前缀' : '签名清单地址'}<input type="url" value={source.url} disabled={locked} spellCheck={false} onChange={event => setDraft({ ...draft, sources: draft.sources.map((item, i) => i === index ? { ...item, url: event.target.value } : item) })} /></label>}
          {resultFor(source) && <p className="update-hint" role="status">{resultFor(source)!.reachable ? `连通 · 响应 ${resultFor(source)!.elapsedMs} ms` : '不可用'} · {resultFor(source)!.detail}</p>}
          <div className="update-actions">
            {index > 0 && <button type="button" disabled={locked} onClick={() => { const sources = [...draft.sources]; [sources[index - 1], sources[index]] = [sources[index], sources[index - 1]]; setDraft({ ...draft, sources }) }}>上移优先级</button>}
            {source.kind !== 'github' && <button type="button" disabled={locked} onClick={() => setDraft({ ...draft, sources: draft.sources.filter((_, i) => i !== index) })}>移除此源</button>}
          </div>
        </div>)}
        <div className="update-actions">
          <button type="button" disabled={locked || draft.sources.length >= 8} onClick={() => setDraft({ ...draft, sources: [...draft.sources, { id: `custom-${Date.now()}`, label: '自定义加速', kind: 'proxy', url: '', enabled: true }] })}>增加加速源</button>
          <button type="button" disabled={locked || draft.sources.length >= 8} onClick={() => setDraft({ ...draft, sources: [...draft.sources, { id: `feed-${Date.now()}`, label: '国内签名清单', kind: 'feed', url: '', enabled: true }] })}>增加国内副本</button>
          <button type="button" disabled={locked} onClick={() => void run(async () => setState(await window.electronAPI.saveUpdatePreferences(draft)))}>保存设置</button>
        </div>
        <p className="update-hint">只填写 HTTPS 地址，不填写 GitHub Token。加速源仅负责转发，清单签名与文件哈希必须通过。公共源速度、额度和可用性不保证；不影响离线混剪。</p>
      </details>}
    </dialog>, document.body)}
  </>
}
