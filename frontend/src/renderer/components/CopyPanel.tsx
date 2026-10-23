import { useEffect, useState } from 'react'
import { api, LlmSettingsInfo } from '../api/client'
import { useStore } from '../store'

const STYLE_PRESETS = ['真人口语化', '痛点开场', '测评对比', '闺蜜推荐', '限时促销']

export default function CopyPanel() {
  const { config, setConfig, addToast } = useStore()

  const [settings, setSettings] = useState<LlmSettingsInfo | null>(null)
  const [baseUrl, setBaseUrl] = useState('')
  const [apiKey, setApiKey] = useState('')
  const [model, setModel] = useState('')
  const [showSettings, setShowSettings] = useState(false)
  const [savingSettings, setSavingSettings] = useState(false)

  const [brief, setBrief] = useState('')
  const [count, setCount] = useState(3)
  const [duration, setDuration] = useState(30)
  const [style, setStyle] = useState(STYLE_PRESETS[0])
  const [generating, setGenerating] = useState(false)
  const [items, setItems] = useState<string[]>([])
  const [savingIndex, setSavingIndex] = useState<number | null>(null)

  useEffect(() => {
    api.getLlmSettings()
      .then(info => {
        setSettings(info)
        setBaseUrl(info.base_url)
        setModel(info.model)
        if (!info.has_key) setShowSettings(true)
      })
      .catch(() => setShowSettings(true))
  }, [])

  const saveSettings = async () => {
    setSavingSettings(true)
    try {
      const info = await api.saveLlmSettings({
        base_url: baseUrl.trim(),
        model: model.trim(),
        api_key: apiKey.trim() || undefined,
      })
      setSettings(info)
      setApiKey('')
      addToast('接口配置已保存', 'success')
      if (info.has_key) setShowSettings(false)
    } catch (error: any) {
      addToast(error?.message || '保存失败', 'error')
    } finally {
      setSavingSettings(false)
    }
  }

  const generate = async () => {
    if (!brief.trim()) {
      addToast('先写点产品信息或卖点', 'warning')
      return
    }
    setGenerating(true)
    try {
      const result = await api.generateCopy({ brief: brief.trim(), count, style, duration })
      setItems(result.items)
      addToast(`生成了 ${result.items.length} 条文案`, 'success')
    } catch (error: any) {
      addToast(error?.message || '生成失败', 'error')
    } finally {
      setGenerating(false)
    }
  }

  const saveAsSrt = async (text: string, index: number) => {
    const directory = (config.srt_dir || '').trim()
    if (!directory) {
      addToast('先在「混剪」页选字幕目录，生成的文案会存进去', 'warning')
      return
    }
    setSavingIndex(index)
    try {
      const result = await api.copyToSrt({ text, directory })
      addToast(`已存为字幕：${result.path.split(/[\\/]/).pop()}（${result.cues} 句）`, 'success')
      if (!config.enable_srt) setConfig({ enable_srt: true })
    } catch (error: any) {
      addToast(error?.message || '保存失败', 'error')
    } finally {
      setSavingIndex(null)
    }
  }

  const copyText = async (text: string) => {
    try {
      await navigator.clipboard.writeText(text)
      addToast('已复制', 'info')
    } catch {
      addToast('复制失败，请手动选中', 'warning')
    }
  }

  const inputClass = 'h-7 min-w-0 rounded-[4px] border border-border/[0.10] bg-background-elev px-2 font-mono text-[11px] text-foreground outline-none placeholder:text-muted-foreground/55 focus:border-accent/70'
  const hasKey = settings?.has_key ?? false

  return (
    <div role="region" aria-label="LLM 文案" className="absolute inset-0 overflow-y-auto bg-background/70 px-5 py-5">
      <div className="flex items-center justify-between border-b border-border/[0.06] pb-4">
        <div>
          <div className="text-[13px] font-semibold text-foreground">智能文案</div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">LLM COPYWRITER</div>
        </div>
        <div className="text-right">
          <div className={`font-mono text-[12px] ${hasKey ? 'text-ok' : 'text-hot'}`}>
            {hasKey ? '已配置' : '未配置'}
          </div>
          <div className="text-[10px] text-muted-foreground">{settings?.model || '—'}</div>
        </div>
      </div>

      {/* 接口配置 */}
      <div className="mt-3 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-2">
        <button
          type="button"
          onClick={() => setShowSettings(value => !value)}
          className="flex w-full items-center justify-between text-[11px] text-foreground/85"
        >
          <span>接口配置（OpenAI 兼容）</span>
          <span className="font-mono text-[10px] text-muted-foreground">{showSettings ? '收起' : '展开'}</span>
        </button>

        {showSettings && (
          <div className="mt-2 space-y-1.5">
            <div className="flex items-center gap-2">
              <span className="w-14 shrink-0 text-[10px] text-muted-foreground">Base URL</span>
              <input aria-label="接口地址" value={baseUrl} onChange={event => setBaseUrl(event.target.value)}
                placeholder="https://api.deepseek.com/v1" className={`${inputClass} flex-1`} />
            </div>
            <div className="flex items-center gap-2">
              <span className="w-14 shrink-0 text-[10px] text-muted-foreground">API Key</span>
              <input aria-label="API Key" type="password" value={apiKey} onChange={event => setApiKey(event.target.value)}
                placeholder={hasKey ? '已保存，留空表示不修改' : 'sk-...'} className={`${inputClass} flex-1`} />
            </div>
            <div className="flex items-center gap-2">
              <span className="w-14 shrink-0 text-[10px] text-muted-foreground">模型</span>
              <input aria-label="模型名" value={model} onChange={event => setModel(event.target.value)}
                placeholder="deepseek-chat" className={`${inputClass} flex-1`} />
            </div>
            <button type="button" onClick={saveSettings} disabled={savingSettings}
              className="mt-1 h-7 w-full rounded-[4px] bg-accent text-[11px] font-semibold text-background hover:bg-accent-hover disabled:opacity-50">
              {savingSettings ? '保存中…' : '保存接口配置'}
            </button>
            <p className="text-[9px] leading-4 text-muted-foreground">
              Base URL 要填到<b className="text-foreground/80">版本号那一级</b>：
              <br />OpenAI / DeepSeek / Kimi → 结尾 <code>/v1</code>
              <br />火山方舟（豆包）→ 结尾 <code>/api/v3</code>　智谱 → <code>/api/paas/v4</code>
            </p>
            <p className="text-[9px] leading-4 text-muted-foreground">
              Key 只存在本机，不会上传。模型名填平台文档里的完整名称。
            </p>
          </div>
        )}
      </div>

      {/* 生成 */}
      <div className="mt-3 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-3">
        <div className="mb-2 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">生成文案</div>
        <textarea
          aria-label="产品信息与卖点"
          value={brief}
          onChange={event => setBrief(event.target.value)}
          rows={4}
          placeholder="写产品和卖点，越具体越好。例如：厨房一次性抹布，干湿两用，一卷50片，主打去油不留痕、用完即弃不发霉，价格19.9三卷。"
          className="w-full resize-none rounded-[4px] border border-border/[0.10] bg-background-elev px-2.5 py-2 text-[11px] leading-5 text-foreground outline-none placeholder:text-muted-foreground/50 focus:border-accent/70"
        />

        <div className="mt-2 flex items-center gap-2">
          <span className="w-10 shrink-0 text-[10px] text-muted-foreground">条数</span>
          <input type="number" min={1} max={10} aria-label="生成条数" value={count}
            onChange={event => setCount(Math.max(1, Math.min(10, Number(event.target.value) || 1)))}
            className={`${inputClass} w-[60px]`} />
          <span className="text-[10px] text-muted-foreground">时长</span>
          <input type="number" min={5} max={180} aria-label="单条时长" value={duration}
            onChange={event => setDuration(Math.max(5, Math.min(180, Number(event.target.value) || 30)))}
            className={`${inputClass} w-[64px]`} />
          <span className="text-[10px] text-muted-foreground">秒</span>
        </div>

        <div className="mt-2 flex flex-wrap items-center gap-1">
          {STYLE_PRESETS.map(preset => (
            <button
              key={preset}
              type="button"
              onClick={() => setStyle(preset)}
              className={`h-6 rounded-[3px] border px-2 text-[10px] transition-colors ${
                style === preset
                  ? 'border-accent bg-accent/15 text-accent'
                  : 'border-border/[0.10] text-muted-foreground hover:border-accent/50 hover:text-foreground'
              }`}
            >
              {preset}
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={generate}
          disabled={generating}
          className="mt-2.5 h-8 w-full rounded-[4px] bg-accent text-[12px] font-semibold text-background transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {generating ? '生成中…' : `生成 ${count} 条文案`}
        </button>
      </div>

      {/* 结果 */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">生成结果</span>
          <span className="text-[10px] text-muted-foreground">{items.length} 条</span>
        </div>

        {items.length === 0 ? (
          <div className="rounded-[5px] border border-dashed border-border/[0.10] px-3 py-6 text-center text-[11px] text-muted-foreground/70">
            还没有文案。填好卖点点生成，满意的可以一键存成字幕。
          </div>
        ) : (
          <div className="space-y-1.5">
            {items.map((text, index) => (
              <div key={index} className="rounded-[5px] border border-border/[0.10] bg-foreground/[0.025] px-3 py-2">
                <div className="flex items-start gap-2">
                  <span className="mt-0.5 shrink-0 font-mono text-[9px] text-muted-foreground">
                    {String(index + 1).padStart(2, '0')}
                  </span>
                  <p className="min-w-0 flex-1 whitespace-pre-wrap text-[11px] leading-5 text-foreground/90">{text}</p>
                </div>
                <div className="mt-1.5 flex items-center gap-1.5 pl-5">
                  <button
                    type="button"
                    onClick={() => saveAsSrt(text, index)}
                    disabled={savingIndex === index}
                    className="rounded-[3px] border border-accent/40 bg-accent/10 px-2 py-0.5 text-[10px] text-accent hover:bg-accent/20 disabled:opacity-50"
                  >
                    {savingIndex === index ? '保存中…' : '存为 SRT'}
                  </button>
                  <button
                    type="button"
                    onClick={() => copyText(text)}
                    className="rounded-[3px] border border-border/[0.10] px-2 py-0.5 text-[10px] text-muted-foreground hover:border-accent/60 hover:text-accent"
                  >
                    复制
                  </button>
                  <span className="ml-auto font-mono text-[9px] text-muted-foreground/70">
                    {text.replace(/\\s/g, '').length} 字
                  </span>
                </div>
              </div>
            ))}
          </div>
        )}

        <p className="mt-3 text-[9px] leading-4 text-muted-foreground">
          {config.srt_dir?.trim()
            ? `存为 SRT 会写进字幕目录：${config.srt_dir.split(/[\\\\/]/).filter(Boolean).at(-1)}，混剪时随机选用。`
            : '还没选字幕目录。到「混剪」页的「字幕」卡片选一个目录，生成的文案就能直接当字幕用。'}
        </p>
      </div>
    </div>
  )
}
