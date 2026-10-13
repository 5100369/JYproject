import fs from 'node:fs'
import path from 'node:path'
import { createHash } from 'node:crypto'
import { DEFAULT_UPDATE_PREFERENCES, UPDATE_FEED, UPDATE_PROBE_ASSET, compareVersions, githubAssetURL, samePackage, normalizePreferences, sourceURL, verifyUpdateManifest } from './updateProtocol'
import type { UpdateActivity, UpdateAsset, UpdateManifest, UpdatePreferences, UpdateSource, UpdateState, UpdateSourceCheck } from '../shared/updates'

interface Options {
  version: string; platform: string; development: boolean; cacheDir: string; settingsFile: string; publicKey: string
  fetcher: (url: string, init?: RequestInit) => Promise<Response>
  activity: () => Promise<UpdateActivity>
  changed: (state: UpdateState) => void
  apply: (installer: string, activity: UpdateActivity) => Promise<boolean>
  intervalMs?: number
  freeBytes?: (directory: string) => Promise<number>
  testMode?: boolean
  initialPreferences?: UpdatePreferences
}
export async function fileDigest(file: string, signal?: AbortSignal): Promise<string> {
  const hash = createHash('sha256')
  const stream = fs.createReadStream(file)
  const abort = () => stream.destroy(new Error('安装包校验已暂停'))
  if (signal?.aborted) abort()
  else signal?.addEventListener('abort', abort, { once: true })
  try { for await (const chunk of stream) hash.update(chunk) }
  finally { signal?.removeEventListener('abort', abort); if (!stream.closed) await new Promise<void>(resolve => stream.once('close', resolve)) }
  return hash.digest('hex')
}
async function freeDiskBytes(directory: string): Promise<number> {
  // Electron 28 includes Node >=18.15. Older @types/node lacks statfs types.
  const statfs = (fs.promises as unknown as { statfs?: (directory: string) => Promise<{ bavail: number; bsize: number }> }).statfs
  if (!statfs) throw new Error('当前运行环境无法检查磁盘余量，暂不下载更新')
  const info = await statfs(directory)
  return Number(info.bavail) * Number(info.bsize)
}
async function boundedJSON(response: Response, limit = 256 * 1024): Promise<unknown> {
  if (!response.ok) { await response.body?.cancel().catch(() => {}); throw new Error(`HTTP ${response.status}`) }
  const reader = response.body?.getReader()
  if (!reader) throw new Error('更新源返回空内容')
  const chunks: Uint8Array[] = []; let length = 0
  try {
    while (true) {
      const chunk = await reader.read()
      if (chunk.done) break
      length += chunk.value.length
      if (length > limit) throw new Error('更新清单过大')
      chunks.push(chunk.value)
    }
    return JSON.parse(Buffer.concat(chunks).toString('utf8'))
  } finally { await reader.cancel().catch(() => {}); reader.releaseLock() }
}

export class UpdateManager {
  private state: UpdateState
  private manifest?: UpdateManifest
  private installer?: string
  private locked = false
  private intent = false
  private epoch = 0
  private pauseReason: 'busy' | 'user' | null = null
  private controller?: AbortController
  private bootTimer?: ReturnType<typeof setTimeout>
  private monitor?: ReturnType<typeof setInterval>
  private disposed = false
  private polling = false
  private lastProgress = 0
  private probing = false
  private probeControllers = new Set<AbortController>()

  constructor(private options: Options) {
    let preferences = structuredClone(options.initialPreferences ?? DEFAULT_UPDATE_PREFERENCES)
    try { preferences = normalizePreferences(JSON.parse(fs.readFileSync(options.settingsFile, 'utf8'))) } catch { /* Defaults only; never touch mixing preferences. */ }
    this.state = { phase: 'idle', currentVersion: options.version, notes: [], message: options.development ? '开发版不自动检查或安装；可以手动检查下载。' : '启动后自动检查，空闲时下载；不会自动重启。',
      received: 0, total: 0, busy: false, resumable: 0, platform: options.platform, development: options.development, testMode: options.testMode === true, preferences }
  }
  snapshot(): UpdateState { return structuredClone(this.state) }
  private update(values: Partial<UpdateState>) {
    if (this.disposed) return
    this.state = { ...this.state, ...values }
    this.options.changed(this.snapshot())
  }
  start() {
    if (!this.options.development && this.state.preferences.autoCheck) this.bootTimer = setTimeout(() => { void this.check() }, 5000)
  }
  dispose() {
    this.disposed = true; this.epoch++; this.intent = false; this.controller?.abort()
    for (const controller of this.probeControllers) controller.abort()
    clearTimeout(this.bootTimer); clearInterval(this.monitor)
  }
  canSwitch(): boolean { return !this.locked && !this.intent && !this.probing && !this.disposed }
  async probeSources(input: UpdatePreferences): Promise<UpdateSourceCheck[]> {
    if (this.locked || this.probing || this.disposed || this.intent) throw new Error('请先暂停更新操作，再检测下载源')
    if (this.options.testMode) throw new Error('请退出演练，再检测真实网络')
    const preferences = normalizePreferences(input)
    this.probing = true
    try {
      if ((await this.activity()).busy) throw new Error('混剪或压测优先，请空闲后检测下载源')
      const results: UpdateSourceCheck[] = []
      // Two at a time, manual only. No background ping or full package download.
      for (let index = 0; index < preferences.sources.length; index += 2) {
        if (this.disposed) throw new Error('检测已取消')
        if ((await this.activity()).busy) throw new Error('生产已开始，已停止检测下载源')
        results.push(...await Promise.all(preferences.sources.slice(index, index + 2).map(source => this.probeSource(source))))
      }
      return results
    } finally { this.probing = false }
  }
  private async probeSource(source: UpdateSource): Promise<UpdateSourceCheck> {
    const start = performance.now(), controller = new AbortController()
    this.probeControllers.add(controller)
    const timeout = setTimeout(() => controller.abort(), 8000)
    let elapsedMs = 0, reachable = false, detail = ''
    try {
      if (source.kind !== 'feed') {
        const response = await this.options.fetcher(sourceURL(source, UPDATE_PROBE_ASSET), {
          signal: controller.signal, cache: 'no-store', headers: { Range: 'bytes=0-1023', 'Accept-Encoding': 'identity' },
        })
        const reader = response.body?.getReader()
        try {
          if (![200, 206].includes(response.status) || !reader) throw new Error(`下载链路 HTTP ${response.status}`)
          if (response.status === 206 && !/^bytes 0-\d+\/\d+$/.test(response.headers.get('content-range') ?? '')) throw new Error('下载链路 Range 响应错误')
          const prefix: number[] = []
          while (prefix.length < 2) {
            const chunk = await reader.read()
            if (chunk.done) break
            for (const byte of chunk.value.subarray(0, 2 - prefix.length)) prefix.push(byte)
          }
          if (prefix[0] !== 0x4d || prefix[1] !== 0x5a) throw new Error('下载链路返回的不是安装包（可能为拦截页）')
          elapsedMs = Math.round(performance.now() - start); reachable = true
        } finally { if (reader) { await reader.cancel().catch(() => {}); reader.releaseLock() } else await response.body?.cancel().catch(() => {}) }
      }
      const response = await this.options.fetcher(sourceURL(source, UPDATE_FEED, true), { signal: controller.signal, cache: 'no-store' })
      const status = response.status
      try {
        const manifest = verifyUpdateManifest(await boundedJSON(response), this.options.publicKey)
        if (source.kind === 'feed') { reachable = true; elapsedMs = Math.round(performance.now() - start) }
        detail = `${source.kind === 'feed' ? '清单连通，安装包链路未测' : '下载链路正常'}；签名清单有效 (${manifest.version})`
      } catch (error) {
        detail = `${reachable ? '下载链路正常；' : ''}${status === 404 ? '更新清单未发布或尚未同步 (404)' : `清单不可用：${(error as Error).message}`}`
      }
    } catch (error) { detail = `${reachable ? '下载链路正常；清单检查失败：' : ''}${controller.signal.aborted ? '请求超时（8 秒）' : (error as Error).message}` }
    finally { clearTimeout(timeout); this.probeControllers.delete(controller) }
    return { id: source.id, url: source.url, kind: source.kind, label: source.label, reachable, elapsedMs: elapsedMs || Math.round(performance.now() - start), detail, checkedAt: new Date().toISOString() }
  }
  savePreferences(input: UpdatePreferences): UpdateState {
    if (this.locked || this.probing || this.intent) throw new Error('请先暂停下载或等待检查完成，再修改下载源')
    if (this.options.testMode) throw new Error('演练不保存正式更新设置')
    const preferences = normalizePreferences(input)
    fs.mkdirSync(path.dirname(this.options.settingsFile), { recursive: true })
    const temporary = this.options.settingsFile + '.tmp'
    fs.writeFileSync(temporary, JSON.stringify(preferences, null, 2), { mode: 0o600 })
    fs.renameSync(temporary, this.options.settingsFile)
    const cancelledQueue = !preferences.autoDownload && this.intent
    if (!preferences.autoDownload) this.intent = false
    this.update({ preferences, ...(cancelledQueue ? { phase: 'paused' as const, message: '自动下载已关闭，可手动下载。' } : {}) })
    return this.snapshot()
  }
  async check(): Promise<void> {
    if (this.locked || this.probing || this.disposed) return
    this.locked = true; this.intent = false
    const epoch = ++this.epoch, previous = this.snapshot()
    this.controller = new AbortController()
    this.update({ phase: 'checking', message: '正在检查各更新源…' })
    try {
      const sources = this.state.preferences.sources.filter(source => source.enabled)
      const results = await Promise.all(sources.map(async source => {
        const controller = new AbortController(), parent = this.controller!
        const abort = () => controller.abort()
        parent.signal.addEventListener('abort', abort, { once: true })
        const timeout = setTimeout(abort, 8000)
        try {
          const response = await this.options.fetcher(sourceURL(source, UPDATE_FEED, true), { signal: controller.signal, cache: 'no-store', headers: { 'Accept': 'application/json' } })
          const manifest = verifyUpdateManifest(await boundedJSON(response), this.options.publicKey)
          return { manifest, label: source.label }
        } catch (error) { return { error: `${source.label}：${(error as Error).message}` } }
        finally { clearTimeout(timeout); parent.signal.removeEventListener('abort', abort) }
      }))
      if (epoch !== this.epoch || this.disposed) return
      const valid = results.filter(result => result.manifest).sort((a, b) => compareVersions(b.manifest!.version, a.manifest!.version))
      if (!valid.length) throw new Error('未取得有效的签名更新清单。旧 Release 可能尚未发布清单；可更换源后重试。\n' + results.map(result => result.error).join('\n'))
      if (valid.some(result => result.manifest!.version === valid[0].manifest!.version && Object.keys(valid[0].manifest!.platforms)
        .some(platform => !samePackage(valid[0].manifest!.platforms[platform], result.manifest!.platforms[platform])))) throw new Error('同版本更新源的安装包信息不一致，已停止更新，请等待发布源同步')
      const chosen = valid[0], manifest = chosen.manifest!
      if (compareVersions(manifest.version, this.options.version) <= 0) {
        this.manifest = undefined; this.installer = undefined
        this.update({ phase: 'current', version: undefined, notes: [], received: 0, total: 0, source: chosen.label, message: '当前已是最新正式版。' })
      } else {
        if (!manifest.platforms[this.options.platform]) throw new Error('当前系统架构没有可用安装包，请查看 Release')
        this.manifest = manifest; this.installer = undefined
        this.update({ phase: 'available', version: manifest.version, notes: manifest.notes, received: 0, total: manifest.platforms[this.options.platform].size, source: chosen.label, message: `发现 ${manifest.version}，可下载更新。` })
      }
    } catch (error) {
      if (epoch === this.epoch && !this.disposed) this.update({ phase: previous.phase === 'ready' ? 'ready' : 'error', message: (error as Error).message })
    } finally {
      this.locked = false
      if (epoch === this.epoch && this.manifest && this.state.phase === 'available' && this.state.preferences.autoDownload && !this.options.development) void this.download()
    }
  }
  private async activity(): Promise<UpdateActivity> {
    let activity: UpdateActivity
    try {
      activity = await this.options.activity()
      if (typeof activity.busy !== 'boolean' || !Number.isSafeInteger(activity.resumable) || activity.resumable < 0) throw new Error('Invalid activity response')
    } catch { activity = { busy: true, resumable: 0 } }
    if (activity.busy !== this.state.busy || activity.resumable !== this.state.resumable) this.update(activity)
    return activity
  }
  private watchActivity() {
    if (this.monitor) return
    this.monitor = setInterval(() => {
      if (this.polling || this.disposed || (!this.intent && this.state.phase !== 'ready')) return
      this.polling = true
      void this.activity().then(activity => {
        if (activity.busy && this.locked && ['downloading', 'verifying'].includes(this.state.phase)) {
          this.pauseReason = 'busy'; this.controller?.abort()
        } else if (!activity.busy && !this.locked && this.intent && ['waiting', 'paused'].includes(this.state.phase)) void this.download()
      }).finally(() => { this.polling = false })
    }, this.options.intervalMs ?? 3000)
  }
  pause() {
    this.intent = false; this.pauseReason = 'user'; this.epoch++; this.controller?.abort()
    this.update({ phase: this.manifest ? 'paused' : 'idle', message: '已暂停，可稍后继续。' })
  }
  private paths(asset: UpdateAsset) {
    const directory = path.join(this.options.cacheDir, asset.sha256)
    const name = this.options.testMode ? 'test-data.bin' : asset.name
    return { directory, file: path.join(directory, name), partial: path.join(directory, name + '.part') }
  }
  private async verify(file: string, asset: UpdateAsset, signal?: AbortSignal) {
    if ((await fs.promises.stat(file)).size !== asset.size || await fileDigest(file, signal) !== asset.sha256) throw new Error('安装包校验失败，已阻止安装')
  }
  private progress(received: number, force = false) {
    if (force || Date.now() - this.lastProgress >= 400) { this.lastProgress = Date.now(); this.update({ received }) }
    else this.state = { ...this.state, received }  // Exact snapshots without IPC on every chunk.
  }
  private async assetURL(source: UpdateSource, asset: UpdateAsset): Promise<string> {
    if (source.kind !== 'feed') return sourceURL(source, githubAssetURL(this.manifest!.version, asset))
    this.controller = new AbortController()
    const timeout = setTimeout(() => this.controller?.abort(), 8000)
    try {
      const response = await this.options.fetcher(source.url, { signal: this.controller.signal, cache: 'no-store' })
      const manifest = verifyUpdateManifest(await boundedJSON(response), this.options.publicKey)
      const candidate = manifest.platforms[this.options.platform]
      if (manifest.version !== this.manifest!.version || !candidate || !samePackage(asset, candidate)) throw new Error('国内副本尚未同步到本版，切换备用源')
      return candidate.url
    } finally { clearTimeout(timeout) }
  }
  private async transfer(url: string, asset: UpdateAsset, partial: string, allowSlow = false) {
    let offset = await fs.promises.stat(partial).then(stat => stat.size).catch(() => 0)
    if (offset > asset.size) { await fs.promises.truncate(partial, 0); offset = 0 }
    this.progress(offset, true)
    if (offset === asset.size) return
    this.controller = new AbortController()
    let timeout: ReturnType<typeof setTimeout>
    const armTimeout = () => { clearTimeout(timeout); timeout = setTimeout(() => this.controller?.abort(), 20000) }
    armTimeout()
    let reader: ReadableStreamDefaultReader<Uint8Array> | undefined
    let handle: fs.promises.FileHandle | undefined
    try {
      const response = await this.options.fetcher(url, { signal: this.controller.signal, headers: { ...(offset ? { Range: `bytes=${offset}-` } : {}), 'Accept-Encoding': 'identity' } })
      reader = response.body?.getReader()
      if (response.status !== 200 && response.status !== 206) throw new Error(`HTTP ${response.status}`)
      if (response.headers.get('content-encoding') && response.headers.get('content-encoding') !== 'identity') throw new Error('下载源不能对安装包进行压缩转换')
      if (response.status === 206) {
        const range = response.headers.get('content-range')?.match(/^bytes (\d+)-(\d+)\/(\d+)$/)
        if (!range || Number(range[1]) !== offset || Number(range[2]) !== asset.size - 1 || Number(range[3]) !== asset.size) throw new Error('下载源断点响应不匹配，已停止')
      } else offset = 0  // A server without Range support must restart, never append a full response.
      const length = response.headers.get('content-length')
      if (length !== null && Number(length) !== asset.size - offset) throw new Error('下载源文件大小不匹配')
      if (!reader) throw new Error('下载源返回空内容')
      handle = await fs.promises.open(partial, offset ? 'a' : 'w', 0o600)
      let received = offset
      let speedAt = Date.now(), speedBytes = offset
      while (true) {
        if (this.pauseReason || this.disposed) throw new Error('下载已暂停')
        const chunk = await reader.read()
        if (chunk.done) break
        armTimeout()
        if (received + chunk.value.length > asset.size) throw new Error('下载源返回内容超出安装包大小')
        let written = 0
        while (written < chunk.value.length) written += (await handle.write(chunk.value, written, chunk.value.length - written)).bytesWritten
        received += chunk.value.length; this.progress(received)
        const elapsed = Date.now() - speedAt
        if (!allowSlow && elapsed >= 8000) {
          if ((received - speedBytes) * 1000 / elapsed < 64 * 1024) throw new Error('下载持续低速，切换备用源')
          speedAt = Date.now(); speedBytes = received
        }
      }
      if (received !== asset.size) throw new Error('下载中断，可继续断点下载')
      this.progress(received, true)
    } finally {
      clearTimeout(timeout!)
      await reader?.cancel().catch(() => {}); reader?.releaseLock()
      await handle?.close()
    }
  }
  async download(): Promise<void> {
    if (this.locked || this.probing || !this.manifest || this.disposed) return
    this.locked = true; this.intent = true; this.pauseReason = null; this.watchActivity()
    this.controller = new AbortController()
    const asset = this.manifest.platforms[this.options.platform], target = this.paths(asset)
    this.update({ phase: 'waiting', message: '等待生产空闲后下载…' })
    try {
      if ((await this.activity()).busy) return
      await fs.promises.mkdir(target.directory, { recursive: true })
      if (fs.existsSync(target.file)) {
        this.update({ phase: 'verifying', message: '正在校验缓存安装包…' })
        try { await this.verify(target.file, asset, this.controller.signal); this.installer = target.file }
        catch (error) { if (this.pauseReason) throw error; await fs.promises.unlink(target.file) }
      }
      const errors: string[] = []
      if (!this.installer) {
        const partialSize = await fs.promises.stat(target.partial).then(stat => Math.min(stat.size, asset.size)).catch(() => 0)
        const free = await (this.options.freeBytes ?? freeDiskBytes)(target.directory)
        const needed = asset.size - partialSize + Math.max(512 * 1024 ** 2, asset.size * 2)
        if (!Number.isFinite(free) || free < needed) throw new Error(`更新磁盘空间不足，请至少保留 ${(needed / 1024 ** 2).toFixed(0)} MB 可用空间，再继续下载。`)
        const sources = this.state.preferences.sources.filter(source => source.enabled)
        for (const [index, source] of sources.entries()) {
          for (let attempt = 0; attempt < 2; attempt++) {
            if (this.pauseReason || this.disposed) throw new Error('下载已暂停')
            if ((await this.activity()).busy) { this.pauseReason = 'busy'; throw new Error('生产任务优先') }
            this.update({ phase: 'downloading', source: source.label, message: `正在从 ${source.label} 下载${attempt ? '（重试）' : ''}…` })
            try {
              await this.transfer(await this.assetURL(source, asset), asset, target.partial, index === sources.length - 1)
              if ((await this.activity()).busy || this.pauseReason) { this.pauseReason ||= 'busy'; throw new Error('生产任务优先') }
              this.update({ phase: 'verifying', message: '下载完成，正在核对安装包…' })
              try { await this.verify(target.partial, asset, this.controller?.signal) }
              catch (error) { if (!this.pauseReason) await fs.promises.unlink(target.partial).catch(() => {}); throw error }
              if (this.pauseReason) throw new Error('校验暂停')
              await fs.promises.rename(target.partial, target.file)
              this.installer = target.file
              break
            } catch (error) {
              if (this.pauseReason || this.disposed) throw error
              errors.push(`${source.label}：${(error as Error).message}`)
            }
          }
          if (this.installer) break
        }
      }
      if (!this.installer) throw new Error('各下载源均失败，可以修改加速地址后继续。\n' + errors.join('\n'))
      this.intent = false
      this.update({ phase: 'ready', received: asset.size, total: asset.size, message: this.options.testMode ? '演练完成：测试数据下载与 SHA256 校验通过。不是安装包，不执行安装。' : this.options.development ? '安装包已验证。开发版不允许安装，请使用正式版或打开安装包目录。' : '安装包已验证，可确认更新；不会自动重启。' })
    } catch (error) {
      this.intent = this.pauseReason === 'busy'
      this.update({ phase: this.pauseReason ? 'paused' : 'error', message: this.pauseReason === 'busy' ? '混剪或压测优先，空闲后继续下载。' : this.pauseReason === 'user' ? '已暂停，已下载部分保留，可继续。' : (error as Error).message })
    } finally { this.locked = false }
  }
  async installerPath(): Promise<string> {
    if (this.options.testMode) throw new Error('演练数据不能作为安装包打开')
    if (!['ready', 'installing'].includes(this.state.phase) || !this.installer || !this.manifest) throw new Error('请先完成下载和校验')
    try { await this.verify(this.installer, this.manifest.platforms[this.options.platform]) }
    catch (error) { this.installer = undefined; this.update({ phase: 'error', message: '缓存安装包已改变，请重新下载。', received: 0 }); throw error }
    return this.installer
  }
  async install(): Promise<void> {
    if (this.options.testMode) throw new Error('演练不执行安装')
    if (this.locked || this.disposed) return
    if (this.options.development) throw new Error('开发版不安装更新；请使用正式版测试安装')
    this.locked = true
    try {
      if ((await this.activity()).busy) throw new Error('有生产任务、压测或停止清理，请等待结束后再安装')
      const installer = await this.installerPath()
      const activity = await this.activity()
      if (activity.busy) throw new Error('有生产任务、压测或停止清理，请等待结束后再安装')
      this.update({ phase: 'installing', message: '等待确认安装…' })
      const applied = await this.options.apply(installer, activity)
      this.update({ phase: 'ready', message: applied && this.options.platform.startsWith('darwin') ? '已打开 DMG，请先退出软件，再将新版拖入应用程序。' : applied ? '已启动安装程序。' : '已取消安装，安装包保留。' })
    } catch (error) { this.update({ phase: this.installer ? 'ready' : 'error', message: (error as Error).message }); throw error }
    finally { this.locked = false }
  }
}
