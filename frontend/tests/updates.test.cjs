const { test } = require('node:test')
const assert = require('node:assert/strict')
const fs = require('node:fs'), os = require('node:os'), path = require('node:path'), Module = require('node:module')
const { generateKeyPairSync, createHash } = require('node:crypto')
const esbuild = require('esbuild')
const { envelope } = require('../../scripts/update-release.cjs')

test('source detection checks real package prefix separately from a missing signed feed', async t => {
  let cancelled = 0, requests = []
  const { manager } = fixture(t, { fetcher: async (url, init) => {
    requests.push({ url, init })
    if (url.includes('update.json')) return new Response(null, { status: 404 })
    return new Response(new ReadableStream({ start(controller) { controller.enqueue(Buffer.from('MZ test')); }, cancel() { cancelled++ } }),
      { status: 206, headers: { 'Content-Range': 'bytes 0-1023/12345' } })
  } })
  const result = await manager.probeSources(manager.snapshot().preferences)
  assert.equal(result.length, 4); assert.equal(cancelled, 4)
  for (const item of result) { assert.equal(item.reachable, true); assert.match(item.detail, /下载链路正常.*404/); assert(item.elapsedMs >= 0) }
  assert.equal(requests.filter(item => item.init.headers?.Range === 'bytes=0-1023').length, 4)
  assert.equal(manager.snapshot().phase, 'idle')
})

test('source detection rejects HTML responses and skips traffic while production is busy', async t => {
  let requests = 0, cancelled = 0, busy = true
  const { manager } = fixture(t, { activity: async () => ({ busy, resumable: 0 }), fetcher: async () => {
    requests++; return new Response(new ReadableStream({ start(controller) { controller.enqueue(Buffer.from('<html>blocked')); }, cancel() { cancelled++ } }))
  } })
  await assert.rejects(manager.probeSources(manager.snapshot().preferences), /混剪或压测/); assert.equal(requests, 0)
  busy = false
  const results = await manager.probeSources(manager.snapshot().preferences)
  assert(results.every(item => !item.reachable && /不是安装包/.test(item.detail)))
  assert.equal(cancelled, 4)
})

test('source detection validates feed signature, not just HTTP 200', async t => {
  const { manager } = fixture(t, { fetcher: async url => response(url.includes('bad') ? { payload: 'bad', signature: 'bad' } : signed()) })
  const results = await manager.probeSources({ autoCheck: false, autoDownload: false, sources: [
    { id: 'valid', kind: 'feed', label: '签名正确', url: 'https://example.com/update.json', enabled: true },
    { id: 'bad', kind: 'feed', label: '签名错误', url: 'https://bad.example.com/update.json', enabled: true },
  ] })
  assert.equal(results[0].reachable, true); assert.match(results[0].detail, /安装包链路未测/)
  assert.equal(results[1].reachable, false); assert.match(results[1].detail, /清单不可用/)
})

test('up to eight sources are accepted without silently changing saved preferences', () => {
  const source = { kind: 'proxy', label: 'test', url: 'https://example.com/', enabled: true }
  const prefs = { autoCheck: false, autoDownload: false, sources: Array.from({ length: 8 }, (_, i) => ({ ...source, id: `s${i}` })) }
  assert.equal(protocol.normalizePreferences(prefs).sources.length, 8)
  assert.throws(() => protocol.normalizePreferences({ ...prefs, sources: [...prefs.sources, { ...source, id: 'ninth' }] }), /1～8/)
})

test('interactive demo runs real pause/Range resume and hashes, never exposes an installer', async t => {
  const { manager, root } = demoFixture(t)
  await manager.check(); assert.equal(manager.snapshot().version, '999.0.0'); assert.equal(manager.snapshot().testMode, true)
  const downloading = manager.download()
  await until(() => manager.snapshot().received > 0); manager.pause(); await downloading
  const received = manager.snapshot().received; assert(received > 0 && received < 1024 * 1024)
  await manager.download(); assert.equal(manager.snapshot().phase, 'ready'); assert.equal(manager.snapshot().received, 1024 * 1024)
  await assert.rejects(manager.install(), /演练/); await assert.rejects(manager.installerPath(), /演练/)
  assert.throws(() => manager.savePreferences(manager.snapshot().preferences), /不保存/)
  const session = fs.readdirSync(root)[0]; assert(!fs.existsSync(path.join(root, session, 'preferences-unused.json')))
})

test('interactive demo offers real fallback and corrupt-signature/hash failures', async t => {
  const fallback = demoFixture(t, 'fallback'); await fallback.manager.check(); await fallback.manager.download()
  assert.equal(fallback.manager.snapshot().phase, 'ready'); assert.equal(fallback.manager.snapshot().source, '演练备用源')
  const signature = demoFixture(t, 'signature'); await signature.manager.check(); await signature.manager.download()
  assert.equal(signature.manager.snapshot().phase, 'error'); assert.equal(signature.manager.snapshot().received, 0)
  assert.match(signature.manager.snapshot().message, /签名验证失败/)
  const hash = demoFixture(t, 'hash'); await hash.manager.check(); await hash.manager.download()
  assert.equal(hash.manager.snapshot().phase, 'error'); assert.match(hash.manager.snapshot().message, /校验失败/)
  assert.throws(() => createUpdateDemo({ development: false }), /仅限开发版/)
})

test('demo honours actual production activity and pauses without installing', async t => {
  let busy = true
  const { manager } = demoFixture(t, 'normal', { delayMs: 2, intervalMs: 5, activity: async () => ({ busy, resumable: 2 }) })
  await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'waiting'); assert.equal(manager.snapshot().received, 0)
  assert.equal(manager.canSwitch(), false)
  busy = false; await until(() => manager.snapshot().phase === 'ready'); assert.equal(manager.snapshot().resumable, 2)
})
function load(source) {
  const filename = path.resolve(__dirname, '../', source)
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module); mod.paths = Module._nodeModulePaths(path.dirname(filename)); mod._compile(result.outputFiles[0].text, filename)
  return mod.exports
}
const protocol = load('src/main/updateProtocol.ts'), { UpdateManager } = load('src/main/updateManager.ts')
const pair = generateKeyPairSync('ed25519'), publicKey = pair.publicKey.export({ type: 'spki', format: 'pem' }).toString()
const data = Buffer.from('synthetic installer bytes; never an executable'), hash = createHash('sha256').update(data).digest('hex')
function manifest(version = '2.8.0') {
  return { schema: 1, version, publishedAt: '2026-10-13T00:00:00Z', notes: ['新增在线更新', '保留混剪配置'], platforms: {
    'win32-x64': { name: `VideoMatrix.Setup.${version}.exe`, url: `https://github.com/w2968066/VideoMatrix--/releases/download/v${version}/VideoMatrix.Setup.${version}.exe`, size: data.length, sha256: hash },
    'darwin-arm64': { name: `VideoMatrix.Setup.${version}-mac.dmg`, url: `https://github.com/w2968066/VideoMatrix--/releases/download/v${version}/VideoMatrix.Setup.${version}-mac.dmg`, size: data.length, sha256: hash },
  } }
}
function signed(value = manifest()) { return envelope(value, pair.privateKey) }
const { createUpdateDemo } = load('src/main/updateDemo.ts')
function demoFixture(t, scenario = 'normal', extra = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-update-demo-test-')), changes = []
  const manager = createUpdateDemo({ development: true, version: '2.7.1', platform: 'win32-x64', directory: root,
    scenario, delayMs: 1, activity: async () => ({ busy: false, resumable: 0 }), changed: state => changes.push(state), ...extra })
  t.after(() => { manager.dispose(); fs.rmSync(root, { recursive: true, force: true }) })
  return { manager, root, changes }
}
function response(value) { return new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } }) }
function fixture(t, overrides = {}) {
  const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-update-test-')), changes = [], calls = [], installs = []
  const manager = new UpdateManager({ version: '2.7.1', platform: 'win32-x64', development: false,
    cacheDir: path.join(root, 'cache'), settingsFile: path.join(root, 'preferences.json'), publicKey,
    fetcher: async (url, init) => { calls.push({ url, init }); return url.includes('update.json') ? response(signed()) : new Response(data) },
    activity: async () => ({ busy: false, resumable: 0 }), changed: value => changes.push(value),
    apply: async (file, activity) => { installs.push({ file, activity }); return true }, ...overrides,
  })
  manager.savePreferences({ autoCheck: false, autoDownload: false, sources: structuredClone(protocol.DEFAULT_UPDATE_PREFERENCES.sources) })
  t.after(() => { manager.dispose(); fs.rmSync(root, { recursive: true, force: true }) })
  return { manager, root, changes, calls, installs }
}
async function until(predicate, limit = 1500) {
  const start = Date.now()
  while (!predicate()) { if (Date.now() - start > limit) throw new Error('Timed out'); await new Promise(resolve => setTimeout(resolve, 10)) }
}

test('manifest verifies publisher signature and exact installer names, hashes and platforms', () => {
  assert.equal(protocol.verifyUpdateManifest(signed(), publicKey).version, '2.8.0')
  const tampered = signed(); tampered.payload = Buffer.from(JSON.stringify(manifest('9.9.9'))).toString('base64')
  assert.throws(() => protocol.verifyUpdateManifest(tampered, publicKey), /签名/)
  assert.throws(() => protocol.verifyUpdateManifest({ payload: '%%%bad', signature: 'bad' }, publicKey), /编码/)
  const unknown = generateKeyPairSync('ed25519')
  assert.throws(() => protocol.verifyUpdateManifest(envelope(manifest(), unknown.privateKey), publicKey), /签名/)
  for (const change of [
    value => { value.platforms['win32-x64'].name = '../bad.exe' },
    value => { value.platforms['win32-x64'].size = 3 * 1024 ** 3 },
    value => { value.platforms['win32-x64'].size = 1.5 },
    value => { value.platforms['win32-x64'].sha256 = 'sha256' },
    value => { value.platforms['win32-x64'].url = 'http://example.com/app.exe' },
    value => { delete value.platforms['darwin-arm64'] },
    value => { value.version = '2.8.0-beta' },
    value => { value.notes = ['x'.repeat(2001)] },
    value => { value.publishedAt = 'not a date' },
  ]) { const value = manifest(); change(value); assert.throws(() => protocol.verifyUpdateManifest(signed(value), publicKey)) }
  assert.equal(protocol.compareVersions('2.10.0', '2.9.9'), 1)
  assert.equal(protocol.compareVersions('2.7.1', '2.7.1'), 0)
})

test('source preferences are bounded, HTTPS-only, contain no credentials and retain a working source', () => {
  for (const url of ['http://example.com/', 'file:///tmp/', 'https://user:token@example.com/', 'https://example.com/#token']) {
    assert.throws(() => protocol.normalizePreferences({ autoCheck: true, autoDownload: true, sources: [{ id: 'a', label: 'A', kind: 'proxy', url, enabled: true }] }))
  }
  assert.throws(() => protocol.normalizePreferences({ autoCheck: true, autoDownload: true, sources: [] }))
  assert.throws(() => protocol.normalizePreferences({ autoCheck: true, autoDownload: true, sources: [{ id: 'a', label: 'A', kind: 'github', url: '', enabled: false }] }))
  const source = protocol.normalizePreferences({ autoCheck: false, autoDownload: false, sources: [{ id: 'a', label: 'A', kind: 'proxy', url: 'https://example.com/proxy', enabled: true }] }).sources[0]
  assert.equal(protocol.sourceURL(source, protocol.UPDATE_FEED), 'https://example.com/proxy/' + protocol.UPDATE_FEED)
})

test('checks sources concurrently and rejects a forged newer version', async t => {
  const { manager } = fixture(t, { fetcher: async url => {
    if (url.startsWith('https://gh-proxy.org/')) { const invalid = signed(); invalid.payload = Buffer.from(JSON.stringify(manifest('9.0.0'))).toString('base64'); return response(invalid) }
    return response(signed())
  } })
  await manager.check(); assert.equal(manager.snapshot().phase, 'available'); assert.equal(manager.snapshot().version, '2.8.0')
})

test('old or equal signed versions are never downloaded', async t => {
  let count = 0
  const { manager } = fixture(t, { fetcher: async () => { count++; return response(signed(manifest('2.7.0'))) } })
  await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'current'); assert.equal(count, protocol.DEFAULT_UPDATE_PREFERENCES.sources.length)
})

test('domestic replicas may use different signed URLs for identical package bytes', async t => {
  const domestic = manifest(); domestic.platforms['win32-x64'].url = 'https://domestic.example.com/app.exe'
  const downloads = []
  const { manager } = fixture(t, { fetcher: async url => {
    if (url.includes('update.json')) return response(signed(url.startsWith('https://domestic.example.com/') ? domestic : manifest()))
    downloads.push(url); return new Response(data)
  } })
  manager.savePreferences({ autoCheck: false, autoDownload: false, sources: [
    { id: 'domestic', label: '国内副本', kind: 'feed', url: 'https://domestic.example.com/update.json', enabled: true },
    { id: 'github', label: 'GitHub', kind: 'github', url: '', enabled: true },
  ] })
  await manager.check(); await manager.download()
  assert.equal(manager.snapshot().phase, 'ready'); assert.deepEqual(downloads, ['https://domestic.example.com/app.exe'])
})

test('stale domestic replicas fall back to the fixed current GitHub release, not old packages', async t => {
  const downloads = []
  const { manager } = fixture(t, { fetcher: async url => {
    if (url.includes('update.json')) return response(signed(url.startsWith('https://domestic.example.com/') ? manifest('2.7.0') : manifest()))
    downloads.push(url); return new Response(data)
  } })
  manager.savePreferences({ autoCheck: false, autoDownload: false, sources: [
    { id: 'domestic', label: '国内副本', kind: 'feed', url: 'https://domestic.example.com/update.json', enabled: true },
    { id: 'github', label: 'GitHub', kind: 'github', url: '', enabled: true },
  ] })
  await manager.check(); await manager.download()
  assert.equal(manager.snapshot().phase, 'ready'); assert.equal(downloads.length, 1)
  assert.match(downloads[0], /github\.com\/w2968066\/VideoMatrix--\/releases\/download\/v2\.8\.0\//)
})

test('conflicting signed package contents for the same version are rejected', async t => {
  const changed = manifest(); changed.platforms['win32-x64'].sha256 = '0'.repeat(64)
  const { manager } = fixture(t, { fetcher: async url => response(signed(url.startsWith('https://gh-proxy.org/') ? changed : manifest())) })
  await manager.check(); assert.equal(manager.snapshot().phase, 'error'); assert.match(manager.snapshot().message, /不一致/)
})

test('unknown architecture and missing signatures fail closed without blocking the application', async t => {
  const first = fixture(t, { platform: 'darwin-x64' }); await first.manager.check(); assert.equal(first.manager.snapshot().phase, 'error')
  const second = fixture(t, { fetcher: async () => response(manifest()) }); await second.manager.check(); assert.equal(second.manager.snapshot().phase, 'error')
  assert.equal(second.installs.length, 0)
})

test('download falls back from unavailable acceleration and verifies before exposing an installer', async t => {
  const calls = []
  const { manager } = fixture(t, { fetcher: async (url, init) => {
    calls.push({ url, init }); if (url.includes('update.json')) return response(signed())
    return url.startsWith('https://gh-proxy.org/') ? new Response('limited', { status: 429 }) : new Response(data)
  } })
  await manager.check(); await manager.download()
  assert.equal(manager.snapshot().phase, 'ready'); assert.equal(manager.snapshot().source, 'GHFast 加速')
  assert.deepEqual(fs.readFileSync(await manager.installerPath()), data)
  assert.equal(calls.filter(call => call.url.endsWith('.exe')).length, 3)
  assert.ok(calls.every(call => !call.init?.headers?.Authorization))
})

test('Range resume continues an existing partial file and ignores unrelated user files', async t => {
  let range
  const { manager, root } = fixture(t, { fetcher: async (url, init) => {
    if (url.includes('update.json')) return response(signed())
    range = init.headers.Range
    return new Response(data.subarray(7), { status: 206, headers: { 'content-range': `bytes 7-${data.length - 1}/${data.length}`, 'content-length': String(data.length - 7) } })
  } })
  const directory = path.join(root, 'cache', hash); fs.mkdirSync(directory, { recursive: true })
  fs.writeFileSync(path.join(directory, manifest().platforms['win32-x64'].name + '.part'), data.subarray(0, 7))
  const unrelated = path.join(root, 'user-video.mp4'); fs.writeFileSync(unrelated, 'unchanged')
  await manager.check(); await manager.download()
  assert.equal(range, 'bytes=7-'); assert.equal(manager.snapshot().phase, 'ready'); assert.equal(fs.readFileSync(unrelated, 'utf8'), 'unchanged')
})

test('servers ignoring Range restart cleanly rather than appending a full file', async t => {
  const { manager, root } = fixture(t)
  const directory = path.join(root, 'cache', hash); fs.mkdirSync(directory, { recursive: true })
  fs.writeFileSync(path.join(directory, manifest().platforms['win32-x64'].name + '.part'), data.subarray(0, 7))
  await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'ready')
  assert.deepEqual(fs.readFileSync(await manager.installerPath()), data)
})

test('incorrect byte ranges and content lengths are rejected', async t => {
  for (const headers of [{ 'content-range': `bytes 9-${data.length - 1}/${data.length}` }, { 'content-range': `bytes 0-${data.length - 1}/${data.length}`, 'content-length': '999' }]) {
    const { manager } = fixture(t, { fetcher: async url => url.includes('update.json') ? response(signed()) : new Response(data, { status: 206, headers }) })
    await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'error'); await assert.rejects(manager.installerPath())
  }
})

test('tampered full files are discarded and retried from zero on the good source', async t => {
  const calls = []
  const { manager } = fixture(t, { fetcher: async (url, init) => {
    if (url.includes('update.json')) return response(signed())
    calls.push({ url, init })
    return new Response(url.startsWith('https://gh-proxy.org/') ? Buffer.alloc(data.length) : data)
  } })
  await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'ready')
  assert.equal(calls.at(-1).init.headers.Range, undefined)
})

test('busy or unavailable task status queues downloads, never starts an installer', async t => {
  const { manager, calls, installs } = fixture(t, { activity: async () => ({ busy: true, resumable: 0 }) })
  await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'waiting')
  assert.equal(calls.filter(call => call.url.endsWith('.exe')).length, 0); assert.equal(installs.length, 0)
  manager.pause(); assert.equal(manager.snapshot().phase, 'paused')
  const other = fixture(t, { activity: async () => { throw new Error('backend unavailable') } })
  await other.manager.check(); await other.manager.download(); assert.equal(other.manager.snapshot().phase, 'waiting')
})

test('a queued download automatically resumes after the task drains', async t => {
  let busy = true
  const { manager } = fixture(t, { intervalMs: 10, activity: async () => ({ busy, resumable: 0 }) })
  await manager.check(); await manager.download(); assert.equal(manager.snapshot().phase, 'waiting')
  busy = false; await until(() => manager.snapshot().phase === 'ready')
})

test('insufficient free space never downloads into the user system drive', async t => {
  const { manager, calls } = fixture(t, { freeBytes: async () => 1024 })
  await manager.check(); await manager.download()
  assert.equal(manager.snapshot().phase, 'error'); assert.match(manager.snapshot().message, /空间不足/)
  assert.equal(calls.filter(call => call.url.endsWith('.exe')).length, 0)
})

test('manual pause cancels a stream, preserves partial bytes, and duplicate starts do not fork downloads', async t => {
  let started, count = 0
  const { manager, root } = fixture(t, { fetcher: async (url, init) => {
    if (url.includes('update.json')) return response(signed())
    count++
    return new Response(new ReadableStream({ start(controller) {
      controller.enqueue(data.subarray(0, 7)); started = true
      init.signal.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')), { once: true })
    } }))
  } })
  await manager.check(); const downloading = manager.download()
  await until(() => started && manager.snapshot().received === 7)
  await manager.download(); manager.pause(); await downloading
  assert.equal(count, 1); assert.equal(manager.snapshot().phase, 'paused')
  assert.equal(fs.statSync(path.join(root, 'cache', hash, manifest().platforms['win32-x64'].name + '.part')).size, 7)
})

test('production starting during download aborts the stream, then resumes only once idle', async t => {
  let busy = false, started = false, transfers = 0
  const { manager } = fixture(t, { intervalMs: 10, activity: async () => ({ busy, resumable: 0 }), fetcher: async (url, init) => {
    if (url.includes('update.json')) return response(signed())
    transfers++
    if (transfers > 1) return new Response(data.subarray(7), { status: 206, headers: { 'content-range': `bytes 7-${data.length - 1}/${data.length}` } })
    return new Response(new ReadableStream({ start(controller) { started = true; controller.enqueue(data.subarray(0, 7)); init.signal.addEventListener('abort', () => controller.error(new DOMException('aborted', 'AbortError')), { once: true }) } }))
  } })
  await manager.check(); const downloading = manager.download(); await until(() => started && manager.snapshot().received === 7)
  busy = true; await downloading; assert.equal(manager.snapshot().phase, 'paused'); assert.equal(transfers, 1)
  busy = false; await until(() => manager.snapshot().phase === 'ready'); assert.equal(transfers, 2)
})

test('install requires fresh idle status, passes stopped-task count, and rechecks cached bytes', async t => {
  let busy = false
  const { manager, installs } = fixture(t, { activity: async () => ({ busy, resumable: 2 }) })
  await manager.check(); await manager.download(); busy = true
  await assert.rejects(manager.install(), /任务|清理/); assert.equal(installs.length, 0)
  busy = false; await manager.install(); assert.equal(installs[0].activity.resumable, 2)
  fs.writeFileSync(installs[0].file, Buffer.alloc(data.length))
  await assert.rejects(manager.install(), /校验/); assert.equal(installs.length, 1)
  assert.equal(manager.snapshot().phase, 'error')
  await manager.download(); assert.equal(manager.snapshot().phase, 'ready')
})

test('ready installer controls recover after a busy or unavailable backend becomes idle', async t => {
  let busy = false
  const { manager } = fixture(t, { intervalMs: 10, activity: async () => ({ busy, resumable: 0 }) })
  await manager.check(); await manager.download(); busy = true
  await assert.rejects(manager.install(), /任务|清理/); assert.equal(manager.snapshot().busy, true)
  busy = false; await until(() => manager.snapshot().busy === false)
  assert.equal(manager.snapshot().phase, 'ready')
})

test('development builds never execute an installer; failed launches retain the working app', async t => {
  const dev = fixture(t, { development: true }); await dev.manager.check(); await dev.manager.download()
  await assert.rejects(dev.manager.install(), /开发版/); assert.equal(dev.installs.length, 0)
  const failed = fixture(t, { apply: async () => { throw new Error('launch failed') } })
  await failed.manager.check(); await failed.manager.download(); await assert.rejects(failed.manager.install(), /launch failed/)
  assert.equal(failed.manager.snapshot().phase, 'ready')
})

test('preferences survive restart independently of rendering configuration', async t => {
  const { manager, root } = fixture(t)
  const config = path.join(root, 'vm-config.json'); fs.writeFileSync(config, '{"target_count":10}')
  const preferences = { autoCheck: false, autoDownload: false, sources: [{ id: 'domestic', kind: 'feed', label: '国内源', url: 'https://example.com/update.json', enabled: true }] }
  manager.savePreferences(preferences)
  const second = new UpdateManager({ version: '2.7.1', platform: 'win32-x64', development: true, publicKey,
    cacheDir: root, settingsFile: path.join(root, 'preferences.json'), fetcher: async () => response(signed()), activity: async () => ({ busy: false, resumable: 0 }), changed: () => {}, apply: async () => false })
  t.after(() => second.dispose())
  assert.deepEqual(second.snapshot().preferences, preferences); assert.equal(fs.readFileSync(config, 'utf8'), '{"target_count":10}')
})
