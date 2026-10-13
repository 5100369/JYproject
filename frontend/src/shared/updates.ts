export interface UpdateAsset { name: string; url: string; size: number; sha256: string }
export interface UpdateManifest {
  schema: 1
  version: string
  publishedAt: string
  notes: string[]
  platforms: Record<string, UpdateAsset>
}
export interface UpdateSource { id: string; label: string; kind: 'github' | 'proxy' | 'feed'; url: string; enabled: boolean }
export interface UpdatePreferences { autoCheck: boolean; autoDownload: boolean; sources: UpdateSource[] }
export interface UpdateActivity { busy: boolean; resumable: number }
export type UpdateTestScenario = 'normal' | 'fallback' | 'signature' | 'hash'
export interface UpdateSourceCheck {
  id: string; url: string; kind: UpdateSource['kind']; label: string
  reachable: boolean; elapsedMs: number; detail: string; checkedAt: string
}
export interface UpdateState {
  phase: 'idle' | 'checking' | 'current' | 'available' | 'waiting' | 'downloading' | 'paused' | 'verifying' | 'ready' | 'installing' | 'error'
  currentVersion: string
  version?: string
  notes: string[]
  message: string
  source?: string
  received: number
  total: number
  busy: boolean
  resumable: number
  platform: string
  development: boolean
  testMode?: boolean
  preferences: UpdatePreferences
}
