// Screenshot the pages built by capture.py and record the demo video.
// Usage: node docs/media/tools/render.mjs <build-dir> <out-dir>
// Needs Playwright with Chromium and ffmpeg (for the MP4/GIF conversion).
import { createRequire } from 'node:module';
import { execFileSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require('playwright');
const [build, out] = process.argv.slice(2).map((p) => path.resolve(p));
const pages = JSON.parse(fs.readFileSync(path.join(build, 'pages.json'), 'utf8'));
fs.mkdirSync(out, { recursive: true });

const browser = await chromium.launch();
for (const p of pages.filter((p) => !p.video)) {
  const page = await browser.newPage({ viewport: { width: 1600, height: 900 }, deviceScaleFactor: 2 });
  await page.goto('file://' + path.join(build, p.html));
  if (p.wait) await page.waitForFunction(() => window.__done === true, null, { timeout: 30000 });
  await page.waitForTimeout(300);
  const el = await page.$('#shot');
  await el.screenshot({ path: path.join(out, `${p.out}.png`), omitBackground: true });
  if (p.svg) {
    const svg = await page.$eval('#shot svg', (s) => s.outerHTML);
    fs.writeFileSync(path.join(out, `${p.out}.svg`), svg);
  }
  console.log('screenshot', p.out);
  await page.close();
}

for (const p of pages.filter((p) => p.video)) {
  const videoDir = path.join(build, 'video');
  const ctx = await browser.newContext({
    viewport: { width: 1200, height: 740 },
    recordVideo: { dir: videoDir, size: { width: 1200, height: 740 } },
  });
  const page = await ctx.newPage();
  await page.goto('file://' + path.join(build, p.html));
  await page.waitForFunction(() => window.__done === true, null, { timeout: 180000, polling: 500 });
  await page.waitForTimeout(500);
  const webm = await page.video().path();
  await ctx.close();
  const mp4 = path.join(out, `${p.out}.mp4`);
  const gif = path.join(out, `${p.out}.gif`);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-vf', 'crop=1200:740:0:0',
    '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-crf', '30', '-preset', 'slow', '-movflags',
    '+faststart', mp4]);
  execFileSync('ffmpeg', ['-y', '-loglevel', 'error', '-i', webm, '-vf',
    'fps=6,scale=820:-1:flags=lanczos,split[a][b];[a]palettegen=max_colors=48[p];[b][p]paletteuse=dither=none',
    gif]);
  console.log('video', mp4, gif);
}
await browser.close();
for (const f of fs.readdirSync(build).filter((f) => f.startsWith('sample-'))) {
  fs.copyFileSync(path.join(build, f), path.join(out, f));
}
