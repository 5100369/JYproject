import { verify } from 'node:crypto'
import type { UpdateAsset, UpdateManifest, UpdatePreferences, UpdateSource } from '../shared/updates'

export const UPDATE_REPO = 'w2968066/VideoMatrix--'
export const UPDATE_FEED = `https://github.com/${UPDATE_REPO}/releases/latest/download/update.json`
// A published package, independent of update.json being available. Read at most
// its first KiB and cancel, never download an installer just to test a source.
export const UPDATE_PROBE_ASSET = `https://github.com/${UPDATE_REPO}/releases/download/v2.7.1/VideoMatrix.Setup.2.7.1.exe`
export function githubAssetURL(version: string, asset: UpdateAsset): string {
  return `https://github.com/${UPDATE_REPO}/releases/download/v${version}/${asset.name}`
}
export function samePackage(a: UpdateAsset, b: UpdateAsset): boolean {
  return a.name === b.name && a.size === b.size && a.sha256 === b.sha256
}
export const DEFAULT_UPDATE_PREFERENCES: UpdatePreferences = {
  autoCheck: true, autoDownload: true,
  sources: [
    { id: 'accelerated', label: 'GitHub 加速', kind: 'proxy', url: 'https://gh-proxy.org/', enabled: true },
    { id: 'ghfast', label: 'GHFast 加速', kind: 'proxy', url: 'https://ghfast.top/', enabled: true },
    { id: 'ghproxy', label: 'GHProxy 加速', kind: 'proxy', url: 'https://ghproxy.net/', enabled: true },
    { id: 'github', label: 'GitHub 原站', kind: 'github', url: '', enabled: true },
  ],
}

export function stableVersion(value: unknown): string {
  if (typeof value !== 'string' || !/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(value)) throw new Error('更新版本号格式无效')
  return value
}
export function compareVersions(a: string, b: string): number {
  const left = stableVersion(a).split('.').map(Number), right = stableVersion(b).split('.').map(Number)
  for (let i = 0; i < 3; i++) if (left[i] !== right[i]) return left[i] > right[i] ? 1 : -1
  return 0
}
export function httpsURL(value: unknown): string {
  if (typeof value !== 'string' || value.length > 2048) throw new Error('更新地址无效')
  const url = new URL(value)
  if (url.protocol !== 'https:' || url.username || url.password || url.hash) throw new Error('更新地址必须为不带凭据的 HTTPS 地址')
  return url.href
}
export function normalizePreferences(input: UpdatePreferences): UpdatePreferences {
  if (!input || !Array.isArray(input.sources) || input.sources.length < 1 || input.sources.length > 8) throw new Error('请配置 1～8 个下载源')
  const ids = new Set<string>()
  const sources = input.sources.map(source => {
    if (!source || !/^[a-zA-Z0-9_-]{1,40}$/.test(source.id) || ids.has(source.id)) throw new Error('下载源编号无效或重复')
    ids.add(source.id)
    if (!['github', 'proxy', 'feed'].includes(source.kind) || typeof source.enabled !== 'boolean' || typeof source.url !== 'string') throw new Error('下载源类型无效')
    const label = String(source.label || '').trim().slice(0, 40)
    if (!label) throw new Error('下载源名称不能为空')
    let url = source.kind === 'github' ? '' : httpsURL(source.url.trim())
    if (source.kind === 'proxy') {
      if (new URL(url).search) throw new Error('加速前缀不能包含查询参数')
      if (!url.endsWith('/')) url += '/'
    }
    return { id: source.id, label, kind: source.kind, url, enabled: source.enabled } as UpdateSource
  })
  if (!sources.some(source => source.enabled)) throw new Error('至少启用一个下载源')
  return { autoCheck: input.autoCheck === true, autoDownload: input.autoDownload === true, sources }
}
export function sourceURL(source: UpdateSource, original: string, feed = false): string {
  if (source.kind === 'feed') return feed ? source.url : original
  return source.kind === 'proxy' ? source.url + original : original
}
export function verifyUpdateManifest(envelope: unknown, publicKey: string): UpdateManifest {
  const input = envelope as { payload?: unknown; signature?: unknown }
  if (!input || typeof input.payload !== 'string' || typeof input.signature !== 'string' || input.payload.length > 180000) throw new Error('更新清单格式无效')
  if (!/^[A-Za-z0-9+/]+={0,2}$/.test(input.payload) || !/^[A-Za-z0-9+/]+={0,2}$/.test(input.signature)) throw new Error('更新清单编码无效')
  const bytes = Buffer.from(input.payload, 'base64'), signature = Buffer.from(input.signature, 'base64')
  if (signature.length !== 64 || !verify(null, bytes, publicKey, signature)) throw new Error('更新清单签名验证失败，已阻止下载')
  const manifest = JSON.parse(bytes.toString('utf8')) as UpdateManifest
  if (manifest.schema !== 1 || !manifest.platforms || !Array.isArray(manifest.notes) || manifest.notes.length > 80) throw new Error('更新清单字段无效')
  stableVersion(manifest.version)
  if (!Number.isFinite(Date.parse(manifest.publishedAt)) || manifest.notes.some(note => typeof note !== 'string' || note.length > 2000)) throw new Error('更新公告格式无效')
  for (const [platform, asset] of Object.entries(manifest.platforms)) {
    const suffix = platform === 'win32-x64' ? '.exe' : platform === 'darwin-arm64' ? '-mac.dmg' : null
    if (!suffix || !asset || asset.name !== `VideoMatrix.Setup.${manifest.version}${suffix}` || !/^[a-f0-9]{64}$/.test(asset.sha256)
      || !Number.isSafeInteger(asset.size) || asset.size <= 0 || asset.size > 2 * 1024 ** 3) throw new Error('更新安装包信息无效')
    httpsURL(asset.url)
  }
  if (!manifest.platforms['win32-x64'] || !manifest.platforms['darwin-arm64']) throw new Error('Win / Mac 安装包尚未齐全')
  return manifest
}
