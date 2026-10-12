// node tools/render.mjs [out.png]: one picture of the scene, drawn by headless Chromium (tools/render.sh sets it up).
// PLAYWRIGHT_CORE names playwright-core's module, CHROME the browser, GPU_ANGLE the backend: swiftshader (the CPU)
// or vulkan (a real GPU). Asked for a GPU and given a CPU renderer, it stops (exit 2) instead of passing on the CPU.
// WIDTH and HEIGHT size the picture (1920 x 1080). Exit 1: the page failed; 2: it could not run.
import { createRequire } from 'node:module';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { dirname, extname, join, normalize, resolve } from 'node:path';

const require = createRequire(import.meta.url);
const { chromium } = require(process.env.PLAYWRIGHT_CORE ?? 'playwright-core');
const root = resolve(new URL('..', import.meta.url).pathname);
const out = resolve(process.argv[2] ?? join(root, 'out/khrushchevka.png'));
const width = Number(process.env.WIDTH ?? 1920);
const height = Number(process.env.HEIGHT ?? 1080);

const ANGLE = {
  swiftshader: ['--use-angle=swiftshader', '--enable-unsafe-swiftshader'],
  vulkan: ['--use-angle=vulkan'],
};
const angle = process.env.GPU_ANGLE || 'swiftshader';
if (!ANGLE[angle]) {
  console.log(`unknown GPU_ANGLE '${angle}': ${Object.keys(ANGLE).join(', ')}`);
  process.exit(2);
}

const TYPES = { '.html': 'text/html', '.js': 'text/javascript' };
// Only what the page loads, and only for a request addressed to this server: a page elsewhere in the browser
// (DNS rebinding) must not read the tree through it.
const SERVED = ['/index.html', '/src/', '/node_modules/three/build/'];
const server = createServer(async (req, res) => {
  if (req.headers.host !== `127.0.0.1:${server.address().port}`) { res.writeHead(403).end(); return; }
  let path = normalize(decodeURIComponent(new URL(req.url, 'http://x').pathname));
  if (path === '/') path = '/index.html';
  const file = join(root, path);
  const served = SERVED.some((prefix) => (prefix.endsWith('/') ? path.startsWith(prefix) : path === prefix));
  if (!served || !TYPES[extname(file)] || !file.startsWith(root + '/')) { res.writeHead(404).end(); return; }
  try {
    const body = await readFile(file);
    res.writeHead(200, { 'content-type': TYPES[extname(file)] }).end(body);
  } catch {
    res.writeHead(404).end();
  }
});
await new Promise((done) => server.listen(0, '127.0.0.1', done));
const url = `http://127.0.0.1:${server.address().port}/`;

let code = 0;
const browser = await chromium.launch({
  executablePath: process.env.CHROME,
  args: [...ANGLE[angle], '--ignore-gpu-blocklist', '--no-sandbox'],
});
try {
  const page = await browser.newPage({ viewport: { width, height }, deviceScaleFactor: 1 });
  const problems = [];
  page.on('pageerror', (err) => problems.push(String(err)));
  page.on('console', (msg) => { if (msg.type() === 'error') problems.push(msg.text()); });
  const started = Date.now();
  await page.goto(url);
  await page.waitForFunction(() => window.__rendered || window.__error, null, { timeout: 120000 });
  const failed = await page.evaluate(() => window.__error || '');
  const drawnOn = await page.evaluate(() => {
    const gl = document.createElement('canvas').getContext('webgl2');
    if (!gl) return 'no WebGL2';
    const info = gl.getExtension('WEBGL_debug_renderer_info');
    return info ? gl.getParameter(info.UNMASKED_RENDERER_WEBGL) : gl.getParameter(gl.RENDERER);
  });
  console.log(`drawn on: ${drawnOn}`);
  if (angle === 'vulkan' && /swiftshader|llvmpipe|software/i.test(drawnOn)) {
    console.log('asked for a GPU and given a CPU renderer: no picture');
    code = 2;
  } else if (failed || problems.length) {
    console.log(`the page failed: ${failed || problems.join('; ')}`);
    code = 1;
  } else {
    await mkdir(dirname(out), { recursive: true });
    await page.screenshot({ path: out });
    console.log(`rendered ${out} (${width} x ${height}) in ${Date.now() - started} ms`);
  }
} finally {
  await browser.close();
  server.close();
}
process.exit(code);
