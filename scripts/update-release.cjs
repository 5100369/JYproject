// Local publisher tooling. Never ship or commit the private key.
const { generateKeyPairSync, createPublicKey, sign } = require('node:crypto')
const fs = require('node:fs')
const path = require('node:path')
const { spawnSync } = require('node:child_process')

function envelope(payload, key) {
  const bytes = Buffer.from(JSON.stringify(payload))
  return { payload: bytes.toString('base64'), signature: sign(null, bytes, key).toString('base64') }
}
async function publish(tag, gh = 'gh', repo = '5100369/JYproject') {
  const version = String(tag).replace(/^v/, '')
  if (!/^(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})\.(0|[1-9]\d{0,5})$/.test(version)) throw new Error('Invalid release version')
  const key = process.env.VIDEOMATRIX_UPDATE_PRIVATE_KEY
  if (!key) throw new Error('Missing VIDEOMATRIX_UPDATE_PRIVATE_KEY; do not publish an unsigned feed')
  const publicKey = createPublicKey(key).export({ type: 'spki', format: 'pem' }).toString()
  const pinned = fs.readFileSync(path.join(__dirname, '../frontend/src/shared/updatePublicKey.ts'), 'utf8').replace(/\r\n/g, '\n')
  if (!pinned.includes(publicKey.trim())) throw new Error('Signing key does not match the public key baked into the app')
  const run = args => {
    const result = spawnSync(gh, args, { encoding: 'utf8' })
    if (result.status !== 0) throw new Error(result.stderr || 'GitHub command failed')
    return result.stdout
  }
  const release = JSON.parse(run(['api', `repos/${repo}/releases/tags/${tag}`]))
  const platforms = {}
  for (const [platform, suffix] of [['win32-x64', '.exe'], ['darwin-arm64', '-mac.dmg']]) {
    const name = `VideoMatrix.Setup.${version}${suffix}`
    const asset = release.assets.find(item => item.name === name && item.state === 'uploaded')
    if (!asset || !asset.size || !/^sha256:[a-f0-9]{64}$/.test(asset.digest || '')) throw new Error(`Missing uploaded, verified asset: ${name}`)
    const base = process.env.VIDEOMATRIX_UPDATE_ASSET_BASE_URL
    const url = base ? `${base.replace(/\/$/, '')}/${tag}/${name}` : `https://github.com/${repo}/releases/download/${tag}/${name}`
    if (new URL(url).protocol !== 'https:') throw new Error('Asset hosting must use HTTPS')
    platforms[platform] = { name, url, size: asset.size, sha256: asset.digest.slice(7) }
  }
  const notes = []
  for (const line of (release.body || '').split(/\r?\n/).filter(line => line.trim()).slice(0, 80)) {
    if (Buffer.byteLength(JSON.stringify([...notes, line.slice(0, 2000)])) > 32 * 1024) break
    notes.push(line.slice(0, 2000))
  }
  const payload = { schema: 1, version, publishedAt: release.published_at || new Date().toISOString(), notes, platforms }
  const target = path.join(__dirname, '../.release-tools/update.json')
  fs.mkdirSync(path.dirname(target), { recursive: true })
  fs.writeFileSync(target, JSON.stringify(envelope(payload, key), null, 2))
  run(['release', 'upload', tag, target, '--repo', repo, '--clobber'])
  console.log(`Signed update feed uploaded for ${version}; both installer digests verified`)
}

if (require.main === module) {
  const [command, argument] = process.argv.slice(2)
  if (command === 'keygen') {
    const target = path.resolve(argument || '.release-tools/update-signing/private.pem')
    if (fs.existsSync(target)) throw new Error('Key already exists; refusing to rotate the trusted key')
    const pair = generateKeyPairSync('ed25519')
    fs.mkdirSync(path.dirname(target), { recursive: true })
    fs.writeFileSync(target, pair.privateKey.export({ type: 'pkcs8', format: 'pem' }), { mode: 0o600, flag: 'wx' })
    console.log(pair.publicKey.export({ type: 'spki', format: 'pem' }).toString())
  } else if (command === 'publish') publish(argument, process.env.VIDEOMATRIX_GH || 'gh').catch(error => { console.error(error.message); process.exitCode = 1 })
  else { console.error('Usage: node scripts/update-release.cjs keygen [private-key-path] | publish vX.Y.Z'); process.exitCode = 1 }
}
module.exports = { envelope, publish }
