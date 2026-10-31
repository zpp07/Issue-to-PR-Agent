import { readFile, mkdir, writeFile, lstat, readdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import { join } from 'node:path';
import { createHash } from 'node:crypto';
import { spawnSync } from 'node:child_process';

const root = fileURLToPath(new URL('../', import.meta.url));
const files = [
  'dist/index.html', 'dist/project.html', 'dist/case.html', 'dist/privacy.html', 'dist/style.css', 'dist/favicon.svg', 'dist/robots.txt', 'dist/workbench.png',
  'deploy/compose.yaml', 'deploy/nginx.conf', 'deploy/preview.env', 'deploy/public.env', 'deploy/Caddyfile',
  'scripts/prepare_release.py', 'scripts/smoke.py', 'scripts/verify_bundle.py', 'README.md', 'ICP_GUIDE.md', 'VERIFICATION.md',
];
const expectedAssets = files.filter(f => f.startsWith('dist/')).map(f => f.slice(5)).sort();
if (JSON.stringify((await readdir(join(root, 'dist'))).sort()) !== JSON.stringify(expectedAssets)) throw Error('Unknown static asset');
const hashes = {};
for (const name of files) {
  const path = join(root, name);
  if (!(await lstat(path)).isFile()) throw Error(`Not a regular file: ${name}`);
  hashes[name] = createHash('sha256').update(await readFile(path)).digest('hex');
}
const artifacts = join(root, 'artifacts');
await mkdir(artifacts, { recursive: true });
const stamp = new Date().toISOString().replace(/[-:.]/g, '');
const filename = `zpcode-site-${stamp}.tar.gz`;
const archive = join(artifacts, filename);
await writeFile(archive, '', { flag: 'wx' });
const manifest = { domain: 'zpcode.online', mode: 'private-preview', created_at: new Date().toISOString(), files: hashes };
await writeFile(join(artifacts, 'MANIFEST.json'), JSON.stringify(manifest, null, 2) + '\n');
const tar = spawnSync('tar', ['-czf', archive, '-C', root, ...files, '-C', artifacts, 'MANIFEST.json'], { encoding: 'utf8' });
if (tar.status !== 0) throw Error(tar.stderr || 'tar failed');
const sha256 = createHash('sha256').update(await readFile(archive)).digest('hex');
await writeFile(archive + '.sha256', `${sha256}  ${filename}\n`, { flag: 'wx' });
console.log(JSON.stringify({ archive, sha256, files: files.length + 1, mode: 'private-preview', cloud_deployed: false }, null, 2));
