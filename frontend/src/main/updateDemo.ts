import fs from 'node:fs'
import path from 'node:path'
import { createHash, generateKeyPairSync, randomBytes, sign } from 'node:crypto'
import { UpdateManager } from './updateManager'
import type { UpdateActivity, UpdateManifest, UpdateState, UpdateTestScenario } from '../shared/updates'

// Isolated transport fixture exercising the real state machine, Range transfer,
// file writes and verification. Never uses the publisher's signing key or network.
export function createUpdateDemo(options: {
  development: boolean; version: string; platform: string; directory: string
  scenario: UpdateTestScenario; activity: () => Promise<UpdateActivity>; changed: (state: UpdateState) => void
  delayMs?: number; intervalMs?: number
}): UpdateManager {
  if (!options.development) throw new Error('更新演练仅限开发版')
  if (!['normal', 'fallback', 'signature', 'hash'].includes(options.scenario)) throw new Error('演练场景无效')
  fs.mkdirSync(options.directory, { recursive: true })
  const directory = fs.mkdtempSync(path.join(options.directory, 'session-'))
  const pair = generateKeyPairSync('ed25519'), bytes = randomBytes(1024 * 1024)
  const version = '999.0.0', sha256 = createHash('sha256').update(bytes).digest('hex')
  const manifest: UpdateManifest = { schema: 1, version, publishedAt: new Date().toISOString(),
    notes: ['虚拟版本 999.0.0，仅用于演练，不存在这样的正式版本。', '1 MB 随机测试数据，不是可执行安装包；可测试暂停、续传、回退与校验。'], platforms: {} }
  for (const [platform, suffix] of [['win32-x64', '.exe'], ['darwin-arm64', '-mac.dmg']]) manifest.platforms[platform] = {
    name: `VideoMatrix.Setup.${version}${suffix}`, size: bytes.length, sha256, url: 'https://update-demo.invalid/data',
  }
  const payload = Buffer.from(JSON.stringify(manifest))
  const envelope = { payload: payload.toString('base64'), signature: sign(null, payload, pair.privateKey).toString('base64') }
  if (options.scenario === 'signature') envelope.signature = Buffer.alloc(64).toString('base64')
  const body = options.scenario === 'hash' ? Buffer.from(bytes) : bytes
  if (options.scenario === 'hash') body[0] ^= 0xff
  return new UpdateManager({ version: options.version, platform: options.platform, development: true, testMode: true,
    cacheDir: path.join(directory, 'cache'), settingsFile: path.join(directory, 'preferences-unused.json'),
    publicKey: pair.publicKey.export({ type: 'spki', format: 'pem' }).toString(),
    activity: options.activity, changed: options.changed, intervalMs: options.intervalMs,
    initialPreferences: { autoCheck: false, autoDownload: false, sources: [
      { id: 'demo-primary', kind: 'proxy', label: '演练主源', url: 'https://update-demo.invalid/', enabled: true },
      { id: 'demo-backup', kind: 'github', label: '演练备用源', url: '', enabled: true },
    ] },
    apply: async () => { throw new Error('演练不执行安装') },
    fetcher: async (url, init) => {
      if (init?.signal?.aborted) throw new Error('演练已暂停')
      if (url.includes('update.json')) return new Response(JSON.stringify(envelope), { headers: { 'Content-Type': 'application/json' } })
      if (options.scenario === 'fallback' && url.startsWith('https://update-demo.invalid/')) return new Response(null, { status: 503 })
      const headers = new Headers(init?.headers), offset = Number(headers.get('range')?.match(/^bytes=(\d+)-$/)?.[1] ?? 0)
      let position = offset, cancelled = false
      const stream = new ReadableStream<Uint8Array>({
        async pull(controller) {
          await new Promise(resolve => setTimeout(resolve, options.delayMs ?? 100))
          if (cancelled) return
          if (init?.signal?.aborted) { controller.error(new Error('演练已暂停')); return }
          const end = Math.min(body.length, position + 16384)
          controller.enqueue(body.subarray(position, end)); position = end
          if (position === body.length) controller.close()
        },
        cancel() { cancelled = true },
      })
      return new Response(stream, { status: offset ? 206 : 200, headers: {
        'Content-Length': String(body.length - offset),
        ...(offset ? { 'Content-Range': `bytes ${offset}-${body.length - 1}/${body.length}` } : {}),
      } })
    },
  })
}
