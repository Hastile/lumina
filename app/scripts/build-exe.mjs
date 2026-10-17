// Builds the Windows installer: freezes the engine with PyInstaller, stages
// FFmpeg from PATH, builds the app, then runs electron-builder.
//   node scripts/build-exe.mjs [--dir]   (--dir: unpacked app only, no installer)
import { execSync } from 'node:child_process'
import { copyFileSync, existsSync, mkdirSync, readdirSync, rmSync } from 'node:fs'
import { dirname, join, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const app = resolve(dirname(fileURLToPath(import.meta.url)), '..')
const engine = resolve(app, '..', 'engine')
const run = (cmd, cwd = app) => {
  console.log(`\n> ${cmd}`)
  execSync(cmd, { cwd, stdio: 'inherit' })
}

run(
  'uv run --with pyinstaller pyinstaller packaging/entry.py --name lumina-engine --onedir --console --noconfirm --clean ' +
    '--collect-submodules lumina --collect-data lumina --distpath build/dist --workpath build/work --specpath build',
  engine
)

const ffmpegExe = execSync('where ffmpeg', { encoding: 'utf8' }).split(/\r?\n/)[0].trim()
const ffmpegDir = dirname(ffmpegExe)
const stage = join(app, 'build-resources', 'ffmpeg')
rmSync(stage, { recursive: true, force: true })
mkdirSync(stage, { recursive: true })
for (const f of readdirSync(ffmpegDir)) {
  if (f === 'ffmpeg.exe' || f === 'ffprobe.exe' || f.toLowerCase().endsWith('.dll')) copyFileSync(join(ffmpegDir, f), join(stage, f))
}
if (!existsSync(join(stage, 'ffprobe.exe'))) throw new Error(`ffprobe.exe not found next to ${ffmpegExe}`)
console.log(`staged FFmpeg from ${ffmpegDir}`)

run('npx electron-vite build')
run(`npx electron-builder --win ${process.argv.includes('--dir') ? '--dir' : ''}`)
