// Actual Electron net.fetch transport, but only local synthetic installers.
const { app, net } = require('electron')
const fs = require('node:fs'), path = require('node:path'), os = require('node:os'), http = require('node:http'), Module = require('node:module')
const assert = require('node:assert/strict'), { createHash, generateKeyPairSync } = require('node:crypto')
const esbuild = require('esbuild'), { envelope } = require('../../scripts/update-release.cjs')
const root = fs.mkdtempSync(path.join(os.tmpdir(), 'vm-updater-electron-'))
app.setPath('userData', root)
function load(source) {
  const filename = path.resolve(__dirname, '../', source)
  const result = esbuild.buildSync({ entryPoints: [filename], bundle: true, platform: 'node', format: 'cjs', packages: 'external', write: false })
  const mod = new Module(filename, module); mod.paths = Module._nodeModulePaths(path.dirname(filename)); mod._compile(result.outputFiles[0].text, filename); return mod.exports
}
async function until(predicate) {
  const start = Date.now()
  while (!predicate()) { if (Date.now() - start > 5000) throw new Error('Electron transport timed out'); await new Promise(resolve => setTimeout(resolve, 20)) }
}
app.whenReady().then(async () => {
  let manager, server
  try {
    const { UpdateManager } = load('src/main/updateManager.ts')
    const pair = generateKeyPairSync('ed25519'), bytes = Buffer.alloc(256 * 1024, 42), hash = createHash('sha256').update(bytes).digest('hex')
    const version = '2.8.0', platforms = {}
    for (const [platform, suffix] of [['win32-x64', '.exe'], ['darwin-arm64', '-mac.dmg']]) platforms[platform] = {
      name: `VideoMatrix.Setup.${version}${suffix}`, size: bytes.length, sha256: hash, url: `https://example.com/VideoMatrix.Setup.${version}${suffix}`,
    }
    const manifest = envelope({ schema: 1, version, publishedAt: '2026-10-13T00:00:00Z', notes: ['Isolated transport fixture'], platforms }, pair.privateKey)
    let busy = false, downloads = 0, resumed = false, installed = false
    server = http.createServer((request, response) => {
      if (request.url === '/manifest') { response.setHeader('content-type', 'application/json'); response.end(JSON.stringify(manifest)); return }
      const match = request.headers.range?.match(/^bytes=(\d+)-$/), offset = match ? Number(match[1]) : 0
      downloads++; resumed ||= offset > 0
      response.writeHead(offset ? 206 : 200, { 'Content-Type': 'application/octet-stream', 'Content-Length': bytes.length - offset,
        ...(offset ? { 'Content-Range': `bytes ${offset}-${bytes.length - 1}/${bytes.length}` } : {}) })
      let position = offset
      const timer = setInterval(() => {
        const next = Math.min(bytes.length, position + 16384)
        response.write(bytes.subarray(position, next)); position = next
        if (position === bytes.length) { clearInterval(timer); response.end() }
      }, 15)
      response.on('close', () => clearInterval(timer))
    })
    await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
    manager = new UpdateManager({ version: '2.7.1', platform: 'win32-x64', development: false, cacheDir: path.join(root, 'installer-cache'), settingsFile: path.join(root, 'preferences.json'),
      publicKey: pair.publicKey.export({ type: 'spki', format: 'pem' }).toString(), intervalMs: 20,
      fetcher: (url, options) => net.fetch(`http://127.0.0.1:${server.address().port}/${url.includes('update.json') ? 'manifest' : 'installer'}`, options),
      activity: async () => ({ busy, resumable: 1 }), changed: () => {}, apply: async () => { installed = true; return true } })
    manager.savePreferences({ autoCheck: false, autoDownload: false, sources: [{ id: 'github', label: 'Test transport', kind: 'github', url: '', enabled: true }] })
    await manager.check(); assert.equal(manager.snapshot().phase, 'available')
    const downloading = manager.download()
    await until(() => manager.snapshot().received > 0)
    busy = true; await downloading
    assert.equal(manager.snapshot().phase, 'paused'); assert.equal(downloads, 1)
    busy = false; await until(() => manager.snapshot().phase === 'ready')
    assert.equal(resumed, true); assert.equal(downloads, 2)
    assert.deepEqual(fs.readFileSync(await manager.installerPath()), bytes)
    await manager.install(); assert.equal(installed, true)
    console.log('Electron transport passed: net.fetch, signed metadata, busy cancellation, real Range resume, closed handles and verified synthetic installer callback; no actual installer executed.')
    manager.dispose(); await new Promise(resolve => server.close(resolve))
    // Chromium still owns its cache until exit. The runner cleans this exact
    // newly created profile after the helper process has terminated.
    console.log(`PROFILE_DIR=${root}`)
    app.exit(0)
  } catch (error) {
    console.error(error); manager?.dispose(); server?.close(); console.log(`PROFILE_DIR=${root}`); app.exit(1)
  }
})
