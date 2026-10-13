// Isolated renderer fixture: no live updater, installer, task or saved user profile.
const { chromium } = require('playwright')
const http = require('node:http'), fs = require('node:fs'), path = require('node:path'), assert = require('node:assert/strict')
async function main() {
  const root = path.resolve(__dirname, '../dist/renderer')
  const server = http.createServer((request, response) => {
    const file = path.resolve(root, '.' + (request.url === '/' ? '/index.html' : request.url.split('?')[0]))
    if (!file.startsWith(root + path.sep) || !fs.existsSync(file)) return response.writeHead(404).end()
    response.setHeader('Content-Type', file.endsWith('.js') ? 'text/javascript' : file.endsWith('.css') ? 'text/css' : 'text/html')
    response.end(fs.readFileSync(file))
  })
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve))
  let browser
  try {
    browser = await chromium.launch({ channel: 'msedge', headless: true })
    const page = await browser.newPage({ viewport: { width: 1280, height: 1080 } }), errors = []
    page.on('pageerror', error => errors.push(error.message))
    await page.route('**/api/**', route => route.fulfill({ json: route.request().url().includes('/health') ? { status: 'ok' } : [] }))
    await page.addInitScript(() => {
      window.updateCalls = []; window.updateListener = null; window.pendingDownload = null
      window.fixtureState = { phase: 'idle', currentVersion: '2.7.1', notes: [], message: '开发预览：以下版本和下载状态为测试数据。', received: 0, total: 0,
        busy: false, resumable: 0, platform: 'win32-x64', development: true, preferences: { autoCheck: true, autoDownload: true, sources: [
          { id: 'accelerated', label: 'GitHub 加速', kind: 'proxy', url: 'https://gh-proxy.org/', enabled: true },
          { id: 'github', label: 'GitHub 原站', kind: 'github', url: '', enabled: true },
        ] } }
      window.emitUpdate = patch => { window.fixtureState = { ...window.fixtureState, ...patch }; window.updateListener?.(structuredClone(window.fixtureState)) }
      window.electronAPI = {
        getBackendPort: async () => 8765,
        getUpdateState: async () => structuredClone(window.fixtureState),
        onUpdateState: listener => { window.updateListener = listener; return () => { window.updateListener = null } },
        getUpdatePresets: async () => [
          { id: 'ghfast', label: 'GHFast 加速', kind: 'proxy', url: 'https://ghfast.top/', enabled: true },
          { id: 'ghproxy', label: 'GHProxy 加速', kind: 'proxy', url: 'https://ghproxy.net/', enabled: true },
        ],
        probeUpdateSources: async preferences => {
          window.updateCalls.push('probe')
          return preferences.sources.map((source, index) => ({ ...source, reachable: index !== 0, elapsedMs: 80 + index, detail: index === 0 ? '请求超时（8 秒）' : '下载链路正常；更新清单未发布或尚未同步 (404)', checkedAt: new Date().toISOString() }))
        },
        setUpdateDemo: async scenario => {
          window.updateCalls.push(['demo', scenario]); window.emitUpdate({ testMode: scenario !== null, phase: 'idle', version: undefined, notes: [], total: 0, received: 0 }); return structuredClone(window.fixtureState)
        },
        checkUpdates: async () => { window.updateCalls.push('check'); window.emitUpdate({ phase: 'available', version: '2.8.0', total: 278078340, notes: ['测试公告：支持多源回退', '<script>window.badUpdate=true</script>'], message: '开发预览：发现测试版本，不执行真实更新。' }) },
        downloadUpdate: async () => { window.updateCalls.push('download'); window.emitUpdate({ phase: 'downloading', received: 111231336, source: '测试加速源', message: '开发预览：模拟下载中。' }); await new Promise(resolve => { window.pendingDownload = resolve }) },
        pauseUpdate: async () => { window.updateCalls.push('pause'); window.emitUpdate({ phase: 'paused', message: '下载暂停，部分数据保留。' }); window.pendingDownload?.(); window.pendingDownload = null },
        installUpdate: async () => { window.updateCalls.push('install') },
        revealUpdate: async () => { window.updateCalls.push('reveal') },
        saveUpdatePreferences: async preferences => { window.updateCalls.push(['save', structuredClone(preferences)]); window.emitUpdate({ preferences }); return structuredClone(window.fixtureState) },
      }
    })
    await page.goto(`http://127.0.0.1:${server.address().port}`)
    await page.getByRole('button', { name: '在线更新设置', exact: true }).click()
    const modal = page.getByRole('dialog', { name: '在线更新', exact: true })
    await modal.getByRole('button', { name: '检查更新', exact: true }).click()
    await modal.getByText('2.8.0', { exact: true }).waitFor()
    assert.equal(await page.evaluate(() => window.badUpdate), undefined)
    await modal.getByRole('button', { name: '下载 / 继续', exact: true }).click()
    await modal.getByRole('button', { name: '暂停', exact: true }).waitFor()
    assert.equal(await modal.getByRole('button', { name: '检查更新', exact: true }).isDisabled(), true)
    assert.equal(await modal.getByRole('progressbar').getAttribute('value'), '111231336')
    await modal.getByRole('button', { name: '暂停', exact: true }).click()
    await modal.getByRole('button', { name: '下载 / 继续', exact: true }).waitFor()
    await modal.getByRole('button', { name: '下载 / 继续', exact: true }).click()
    await page.evaluate(() => { window.emitUpdate({ phase: 'ready', received: window.fixtureState.total, message: '开发预览：安装包验证通过。开发版不执行安装。' }); window.pendingDownload?.() })
    const install = modal.getByRole('button', { name: '确认安装更新', exact: true })
    await install.waitFor(); assert.equal(await install.isDisabled(), true)
    await modal.getByRole('button', { name: '安装包目录', exact: true }).click()
    await modal.locator('.update-sources > summary').click()
    await modal.getByLabel('启动自动检查（下次启动生效）').uncheck()
    await modal.getByRole('button', { name: '增加加速源', exact: true }).click()
    await modal.getByRole('textbox', { name: 'HTTPS 加速前缀', exact: true }).nth(1).fill('https://mirror.example.com/')
    await modal.getByRole('button', { name: '保存设置', exact: true }).click()
    assert.equal(await page.evaluate(() => window.fixtureState.preferences.autoCheck), false)
    assert.equal(await page.evaluate(() => window.fixtureState.preferences.sources.length), 3)
    await modal.getByRole('button', { name: '移除此源', exact: true }).last().click()
    await modal.getByRole('button', { name: '保存设置', exact: true }).click()
    await modal.getByRole('button', { name: '补充预设加速源', exact: true }).click()
    assert.equal(await modal.getByText('GHFast 加速', { exact: true }).count(), 1)
    await modal.getByRole('button', { name: '检测全部源', exact: true }).click()
    await modal.getByText('不可用 · 请求超时（8 秒）', { exact: true }).waitFor()
    assert.equal(await modal.getByText(/连通 · 响应/).count(), 3)
    await modal.getByRole('button', { name: '按响应时间排序', exact: true }).click()
    assert.equal(await modal.locator('.update-source').first().getByText('GitHub 原站', { exact: true }).count(), 1)
    await modal.getByRole('button', { name: '保存设置', exact: true }).click()
    const bounds = await modal.boundingBox()
    assert(bounds.x >= 0 && bounds.y >= 0 && bounds.x + bounds.width <= 1280 && bounds.y + bounds.height <= 1080)
    await page.evaluate(() => window.emitUpdate({ notes: ['测试公告：启动检查、空闲下载、确认后安装。', '支持下载源回退、断点续传与安装包验证。'] }))
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-update-dark.png') })
    await page.keyboard.press('Escape'); assert.equal(await modal.isVisible(), false)
    await page.getByRole('button', { name: 'Light', exact: true }).click()
    await page.getByRole('button', { name: '在线更新设置', exact: true }).click()
    const color = await modal.getByRole('textbox', { name: 'HTTPS 加速前缀', exact: true }).first().evaluate(input => getComputedStyle(input).backgroundColor)
    assert.equal(color, 'rgb(244, 245, 247)')
    await page.screenshot({ path: path.resolve(__dirname, '../dist/ui-update-light.png') })
    await page.evaluate(() => window.emitUpdate({ development: false, busy: true }))
    assert.equal(await install.isDisabled(), true)
    await page.evaluate(() => window.emitUpdate({ busy: false, resumable: 2 }))
    await install.click()
    assert.equal(await page.evaluate(() => window.updateCalls.filter(call => call === 'install').length), 1)
    await page.evaluate(() => window.emitUpdate({ phase: 'error', message: '签名验证失败，已阻止下载。' }))
    await modal.getByRole('status').filter({ hasText: '签名验证失败' }).waitFor()
    assert.equal(await modal.getByRole('button', { name: '确认安装更新', exact: true }).count(), 0)
    await page.evaluate(() => window.emitUpdate({ development: true }))
    await modal.getByLabel('演练场景', { exact: true }).selectOption('fallback')
    await modal.getByRole('button', { name: '进入更新演练', exact: true }).click()
    assert.equal(await modal.locator('.update-sources').count(), 0)
    await page.evaluate(() => window.emitUpdate({ phase: 'ready', version: '999.0.0', received: 1048576, total: 1048576 }))
    assert.equal(await modal.getByRole('button', { name: '确认安装更新', exact: true }).count(), 0)
    assert.equal(await modal.getByRole('button', { name: '安装包目录', exact: true }).count(), 0)
    await modal.getByRole('button', { name: '退出演练', exact: true }).click()
    assert.equal(await page.evaluate(() => window.fixtureState.testMode), false)
    await page.keyboard.press('Escape')
    assert.equal(await page.getByRole('button', { name: '启动渲染', exact: true }).isEnabled(), true)
    assert.deepEqual(errors, [])
    console.log('Update UI passed: dark/light, check/download/pause, source preferences, developer/busy installation protection, safe text and non-blocking failure.')
  } finally { await browser?.close(); await new Promise(resolve => server.close(resolve)) }
}
main().catch(error => { console.error(error); process.exitCode = 1 })
