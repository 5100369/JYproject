import { useEffect, useState } from 'react'
import { api, ScheduleItem, SchedulePayload, ScheduleTrigger } from '../api/client'
import { Checkbox } from './ui/checkbox'
import { useStore } from '../store'

const WEEKDAY_TEXT = ['一', '二', '三', '四', '五', '六', '日']

const TRIGGERS: { value: ScheduleTrigger; label: string; hint: string }[] = [
  { value: 'once', label: '单次', hint: '指定日期和时间执行一次，执行后自动停用' },
  { value: 'daily', label: '每天', hint: '每天固定时刻执行' },
  { value: 'weekly', label: '每周', hint: '每周固定几天执行' },
  { value: 'interval', label: '间隔', hint: '每隔 N 分钟执行一次' },
]

const pad = (value: number) => String(value).padStart(2, '0')
const dateValue = (date: Date) => `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}`
const timeValue = (date: Date) => `${pad(date.getHours())}:${pad(date.getMinutes())}`

function defaultOnce() {
  const date = new Date(Date.now() + 30 * 60 * 1000)
  date.setSeconds(0, 0)
  return { date: dateValue(date), time: timeValue(date) }
}

function formatMoment(iso: string, withDate = true) {
  if (!iso) return '—'
  const date = new Date(iso)
  if (Number.isNaN(date.getTime())) return '—'
  const clock = `${pad(date.getHours())}:${pad(date.getMinutes())}`
  if (!withDate) return clock
  const today = new Date()
  if (date.toDateString() === today.toDateString()) return `今天 ${clock}`
  const tomorrow = new Date(today.getTime() + 86400000)
  if (date.toDateString() === tomorrow.toDateString()) return `明天 ${clock}`
  return `${date.getMonth() + 1}月${date.getDate()}日 ${clock}`
}

function formatCountdown(iso: string, now: number) {
  const target = new Date(iso).getTime()
  if (!Number.isFinite(target)) return ''
  const diff = Math.round((target - now) / 1000)
  if (diff <= 0) return '即将执行'
  const days = Math.floor(diff / 86400)
  const hours = Math.floor((diff % 86400) / 3600)
  const minutes = Math.floor((diff % 3600) / 60)
  const seconds = diff % 60
  if (days > 0) return `${days} 天 ${hours} 小时后`
  if (hours > 0) return `${hours} 小时 ${minutes} 分后`
  if (minutes > 0) return `${minutes} 分 ${seconds} 秒后`
  return `${seconds} 秒后`
}

function describeRule(schedule: ScheduleItem) {
  if (schedule.trigger_type === 'once') return `单次 · ${formatMoment(schedule.run_at)}`
  if (schedule.trigger_type === 'daily') return `每天 · ${schedule.time_of_day}`
  if (schedule.trigger_type === 'weekly') {
    const days = schedule.weekdays.map(day => `周${WEEKDAY_TEXT[day - 1]}`).join('、') || '未选日期'
    return `${days} · ${schedule.time_of_day}`
  }
  return `每 ${schedule.interval_minutes} 分钟`
}

const RESULT_STYLE: Record<string, string> = {
  started: 'text-ok',
  busy: 'text-accent',
  missed: 'text-hot',
  error: 'text-hot',
}

export default function SchedulePanel({ schedules, reload }: { schedules: ScheduleItem[]; reload: () => void }) {
  const { config, addToast } = useStore()
  const [name, setName] = useState('')
  const [trigger, setTrigger] = useState<ScheduleTrigger>('once')
  const [once, setOnce] = useState(defaultOnce)
  const [time, setTime] = useState('09:00')
  const [weekdays, setWeekdays] = useState<number[]>([1, 3, 5])
  const [intervalMinutes, setIntervalMinutes] = useState(60)
  const [grace, setGrace] = useState(30)
  const [busy, setBusy] = useState(false)
  const [now, setNow] = useState(Date.now())

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000)
    return () => clearInterval(timer)
  }, [])

  const activeCount = schedules.filter(item => item.enabled).length
  const hookLabel = config.hook_dir.trim() ? config.hook_dir.split(/[\\/]/).filter(Boolean).at(-1) : ''

  const toggleWeekday = (day: number) => {
    setWeekdays(current => (current.includes(day) ? current.filter(item => item !== day) : [...current, day].sort()))
  }

  const submit = async () => {
    if (!config.hook_dir.trim()) {
      addToast('请先到「混剪」页选好首段素材目录', 'warning')
      return
    }
    if (trigger === 'once' && (!once.date || !once.time)) {
      addToast('请填写执行日期和时间', 'warning')
      return
    }
    if (trigger === 'weekly' && weekdays.length === 0) {
      addToast('每周任务至少要选一天', 'warning')
      return
    }

    const payload: SchedulePayload = {
      name: name.trim() || `定时任务 ${activeCount + 1}`,
      config,
      trigger_type: trigger,
      time_of_day: time,
      weekdays,
      interval_minutes: intervalMinutes,
      grace_minutes: grace,
      run_at: `${once.date}T${once.time}:00`,
    }
    setBusy(true)
    try {
      const created = await api.createSchedule(payload)
      addToast(`已排定：${created.name}`, 'success')
      setName('')
      reload()
    } catch (error: any) {
      addToast(error?.message || '创建定时任务失败', 'error')
    } finally {
      setBusy(false)
    }
  }

  const toggleEnabled = async (schedule: ScheduleItem, enabled: boolean) => {
    try {
      await api.updateSchedule(schedule.id, { enabled })
      reload()
    } catch (error: any) {
      addToast(error?.message || '修改失败', 'error')
    }
  }

  const runNow = async (schedule: ScheduleItem) => {
    try {
      await api.runScheduleNow(schedule.id)
      addToast(`已立即执行：${schedule.name}`, 'success')
      reload()
    } catch (error: any) {
      addToast(error?.message || '启动失败', 'error')
    }
  }

  const remove = async (schedule: ScheduleItem) => {
    if (!window.confirm(`删除定时任务「${schedule.name}」？已生成的成片不受影响。`)) return
    try {
      await api.deleteSchedule(schedule.id)
      addToast('已删除', 'info')
      reload()
    } catch (error: any) {
      addToast(error?.message || '删除失败', 'error')
    }
  }

  const inputClass = 'h-7 min-w-0 rounded-[4px] border border-border/[0.10] bg-background-elev px-2 font-mono text-[11px] text-foreground outline-none focus:border-accent/70'

  return (
    <div role="region" aria-label="定时混剪" className="absolute inset-0 overflow-y-auto bg-background/70 px-5 py-5">
      <div className="flex items-center justify-between border-b border-border/[0.06] pb-4">
        <div>
          <div className="text-[13px] font-semibold text-foreground">定时混剪</div>
          <div className="mt-1 font-mono text-[10px] text-muted-foreground">SCHEDULED MIX</div>
        </div>
        <div className="text-right">
          <div className="font-mono text-[15px] text-accent">{activeCount}</div>
          <div className="text-[10px] text-muted-foreground">启用中</div>
        </div>
      </div>

      {/* 新建 */}
      <div className="mt-4 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] px-3 py-3">
        <div className="mb-2.5 text-[10px] uppercase tracking-[0.16em] text-muted-foreground">新建定时任务</div>

        <div className="mb-2 flex items-center gap-2">
          <span className="w-12 shrink-0 text-[11px] text-foreground/80">名称</span>
          <input
            aria-label="定时任务名称"
            value={name}
            onChange={event => setName(event.target.value)}
            placeholder={`定时任务 ${activeCount + 1}`}
            className={`${inputClass} flex-1`}
          />
        </div>

        <div className="mb-2">
          <div className="mb-1.5 flex items-center justify-between">
            <span className="text-[11px] text-foreground/80">触发方式</span>
            <span className="text-[9px] text-muted-foreground">{TRIGGERS.find(item => item.value === trigger)?.hint}</span>
          </div>
          <div className="grid grid-cols-4 gap-1 rounded-[5px] border border-border/[0.08] bg-foreground/[0.02] p-1">
            {TRIGGERS.map(item => (
              <button
                key={item.value}
                type="button"
                onClick={() => setTrigger(item.value)}
                className={`h-6 rounded-[3px] text-[11px] font-medium transition-colors ${
                  trigger === item.value ? 'bg-accent text-background' : 'text-muted-foreground hover:text-foreground'
                }`}
              >
                {item.label}
              </button>
            ))}
          </div>
        </div>

        {trigger === 'once' && (
          <div className="mb-2 flex items-center gap-2">
            <span className="w-12 shrink-0 text-[11px] text-foreground/80">时间</span>
            <input type="date" aria-label="执行日期" value={once.date}
              onChange={event => setOnce(current => ({ ...current, date: event.target.value }))}
              className={`${inputClass} w-[130px]`} />
            <input type="time" aria-label="执行时刻" value={once.time}
              onChange={event => setOnce(current => ({ ...current, time: event.target.value }))}
              className={`${inputClass} w-[96px]`} />
          </div>
        )}

        {(trigger === 'daily' || trigger === 'weekly') && (
          <div className="mb-2 flex items-center gap-2">
            <span className="w-12 shrink-0 text-[11px] text-foreground/80">时刻</span>
            <input type="time" aria-label="执行时刻" value={time}
              onChange={event => setTime(event.target.value)}
              className={`${inputClass} w-[96px]`} />
            {trigger === 'weekly' && (
              <div className="flex flex-1 items-center gap-1">
                {WEEKDAY_TEXT.map((label, index) => {
                  const day = index + 1
                  const active = weekdays.includes(day)
                  return (
                    <button
                      key={day}
                      type="button"
                      onClick={() => toggleWeekday(day)}
                      className={`h-6 w-6 rounded-[3px] border text-[10px] transition-colors ${
                        active
                          ? 'border-accent bg-accent/15 text-accent'
                          : 'border-border/[0.10] text-muted-foreground hover:border-accent/50 hover:text-foreground'
                      }`}
                    >
                      {label}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
        )}

        {trigger === 'interval' && (
          <div className="mb-2 flex items-center gap-2">
            <span className="w-12 shrink-0 text-[11px] text-foreground/80">间隔</span>
            <input type="number" min={1} aria-label="间隔分钟数" value={intervalMinutes}
              onChange={event => setIntervalMinutes(Math.max(1, Number(event.target.value) || 1))}
              className={`${inputClass} w-[76px]`} />
            <span className="text-[10px] text-muted-foreground">分钟执行一次（从保存时起算）</span>
          </div>
        )}

        <div className="mb-2 flex items-center gap-2">
          <span className="w-12 shrink-0 text-[11px] text-foreground/80">补跑</span>
          <input type="number" min={0} aria-label="补跑窗口分钟" value={grace}
            onChange={event => setGrace(Math.max(0, Number(event.target.value) || 0))}
            className={`${inputClass} w-[76px]`} />
          <span className="text-[10px] text-muted-foreground">分钟内错过仍执行；设备忙也在此期间等空档</span>
        </div>

        <div className="mb-2.5 rounded-[4px] border border-border/[0.06] bg-foreground/[0.015] px-2.5 py-2 text-[10px] leading-4 text-muted-foreground">
          使用「混剪」页当前配置的快照：{hookLabel ? `${hookLabel} · ${config.target_count} 条 · ${config.concurrent_tasks ?? 3} 路并发` : '还没选首段素材'}
          <br />创建后再改左侧参数，不会影响已排定的任务。
        </div>

        <button
          type="button"
          onClick={submit}
          disabled={busy}
          className="h-8 w-full rounded-[4px] bg-accent text-[12px] font-semibold text-background transition-colors hover:bg-accent-hover disabled:opacity-50"
        >
          {busy ? '正在添加…' : '添加到定时任务'}
        </button>
      </div>

      {/* 列表 */}
      <div className="mt-4">
        <div className="mb-2 flex items-center justify-between">
          <span className="text-[10px] uppercase tracking-[0.16em] text-muted-foreground">已排定</span>
          <span className="text-[10px] text-muted-foreground">{schedules.length} 条</span>
        </div>

        {schedules.length === 0 ? (
          <div className="rounded-[5px] border border-dashed border-border/[0.10] px-3 py-6 text-center text-[11px] text-muted-foreground/70">
            还没有定时任务。设一个时间，到点会自动按当前配置开始混剪。
          </div>
        ) : (
          <div className="space-y-1.5">
            {schedules.map(schedule => (
              <div
                key={schedule.id}
                className={`rounded-[5px] border px-3 py-2 transition-colors ${
                  schedule.enabled ? 'border-border/[0.10] bg-foreground/[0.025]' : 'border-border/[0.05] bg-foreground/[0.01] opacity-60'
                }`}
              >
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="flex items-center gap-2">
                      <span className={`h-1.5 w-1.5 shrink-0 rounded-full ${schedule.enabled ? 'bg-ok' : 'bg-muted-foreground/40'}`} />
                      <span className="truncate text-[11.5px] font-medium text-foreground">{schedule.name}</span>
                      {!schedule.enabled && <span className="shrink-0 text-[9px] text-muted-foreground">已停用</span>}
                    </div>
                    <div className="mt-1 text-[10px] text-muted-foreground">{describeRule(schedule)}</div>
                    <div className="mt-0.5 flex flex-wrap items-center gap-x-2 font-mono text-[9.5px]">
                      {schedule.enabled && schedule.next_run ? (
                        <span className="text-accent">
                          {formatCountdown(schedule.next_run, now)} · {formatMoment(schedule.next_run)}
                        </span>
                      ) : (
                        <span className="text-muted-foreground/70">未安排下次执行</span>
                      )}
                      {schedule.last_message && (
                        <span className={RESULT_STYLE[schedule.last_result] || 'text-muted-foreground/70'}>
                          {schedule.last_message}
                        </span>
                      )}
                    </div>
                  </div>
                  <div className="flex shrink-0 items-center gap-2 pt-0.5">
                    <Checkbox
                      aria-label={`启用定时任务 ${schedule.name}`}
                      checked={schedule.enabled}
                      onCheckedChange={value => toggleEnabled(schedule, value === true)}
                    />
                    <button
                      type="button"
                      onClick={() => runNow(schedule)}
                      title="立即按这个任务的配置执行一次，不改动原定时间"
                      className="rounded-[3px] border border-border/[0.10] px-1.5 py-0.5 text-[10px] text-foreground/80 hover:border-accent/60 hover:text-accent"
                    >
                      立即
                    </button>
                    <button
                      type="button"
                      onClick={() => remove(schedule)}
                      className="rounded-[3px] border border-border/[0.10] px-1.5 py-0.5 text-[10px] text-muted-foreground hover:border-hot/60 hover:text-hot"
                    >
                      删除
                    </button>
                  </div>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
