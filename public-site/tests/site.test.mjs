import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile, readdir } from 'node:fs/promises';
import { join } from 'node:path';
import { once } from 'node:events';
import { root, routes, createServer } from '../scripts/preview.mjs';

const pages = ['index.html', 'project.html', 'case.html', 'privacy.html'];
const contents = Object.fromEntries(await Promise.all(pages.map(async (file) => [file, await readFile(join(root, file), 'utf8')])));

test('public assets use an exact source allowlist', async () => {
  assert.deepEqual((await readdir(root)).sort(), [...pages, 'style.css', 'favicon.svg', 'robots.txt', 'workbench.png'].sort());
});
for (const page of pages) {
  test(`${page}: accessible structure, accurate preview state, no active application`, () => {
    const html = contents[page];
    assert.match(html, /<html lang="zh-CN">/);
    assert.match(html, /<meta name="viewport"/);
    assert.match(html, /<title>[^<]*代码实践手记[^<]*<\/title>/);
    assert.equal((html.match(/<h1[ >]/g) || []).length, 1);
    assert.match(html, /<main id="main"/);
    assert.match(html, /href="#main"/);
    assert.match(html, /noindex,nofollow/);
    assert.match(html, /ICP 备案尚未完成/);
    assert.equal((html.match(/<!-- filing:start -->/g) || []).length, 1);
    assert.doesNotMatch(html, /<(?:script|iframe|form|input|button|video|audio)\b/i);
    assert.doesNotMatch(html, /\bon\w+\s*=/i);
    assert.doesNotMatch(html, /(?:sk-[a-zA-Z0-9]{15,}|ghp_[a-zA-Z0-9]+|BEGIN .*PRIVATE KEY|C:\\Users|\/home\/ubuntu|43\.142\.123\.91)/);
  });
  test(`${page}: internal files and anchors resolve; external links are approved`, () => {
    for (const match of htmlLinks(contents[page])) {
      const href = match[1];
      if (href.startsWith('https://')) {
        assert.ok(new Set([
          'https://github.com/zpp07/Issue-to-PR-Agent',
          'https://github.com/zpp07/Issue-to-PR-Agent/blob/master/benchmark/real/experiments/E2_REPORT.md',
          'https://github.com/UKGovernmentBEIS/inspect_ai/issues/5556',
          'https://github.com/UKGovernmentBEIS/inspect_ai/issues/5578',
          'https://github.com/UKGovernmentBEIS/inspect_ai/issues/5580',
          'https://beian.miit.gov.cn/',
        ]).has(href), `unapproved external link ${href}`);
        continue;
      }
      assert.ok(href.startsWith('/') || href.startsWith('#'), href);
      const [path, id] = href.split('#');
      const target = path ? routes.get(path) : [page];
      assert.ok(target, `missing route ${href}`);
      if (id) assert.ok(contents[target[0]]?.includes(`id="${id}"`), `missing anchor ${href}`);
    }
  });
}
function htmlLinks(html) { return html.matchAll(/(?:href|src)="([^"]+)"/g); }

test('every page has a first-screen filing banner that stays sticky', async () => {
  for (const html of Object.values(contents)) {
    assert.equal((html.match(/class="filing-banner wrap"/g) || []).length, 1);
    assert.ok(html.indexOf('class="filing-banner wrap"') < html.indexOf('<main id="main"'));
    assert.match(html, /href="https:\/\/beian\.miit\.gov\.cn\/" rel="noreferrer">赣ICP备2026027949号/);
  }
  const css = await readFile(join(root, 'style.css'), 'utf8');
  assert.match(css, /\.site-footer \.filing a\{[^}]*font-size:18px/);
  assert.match(css, /\.filing-banner>a\{[^}]*min-height:44px/);
  assert.match(css, /\.filing-banner\{[^}]*position:sticky;top:0;z-index:30/);
});

test('showcase content has evidence boundaries and excludes resume contact data', async () => {
  assert.match(contents['project.html'], /36 次正式运行/);
  assert.match(contents['project.html'], /非当前模型表现/);
  assert.match(contents['project.html'], /它不证明某次修复或真实 PR 创建成功/);
  assert.match(contents['project.html'], /不是 Agent 主程序已迁移 MySQL/);
  assert.match(contents['index.html'], /不将他人的修复补丁记作自己的代码贡献/);
  for (const html of Object.values(contents)) assert.doesNotMatch(html, /13013083218|zp070219@mail\.ustc\.edu\.cn|\.pdf["']|\.tex["']/);
  const image = await readFile(join(root, 'workbench.png'));
  assert.equal(image.subarray(0, 8).toString('hex'), '89504e470d0a1a0a');
  assert.deepEqual(image, await readFile(new URL('../../docs/images/local-ui-new-task.png', import.meta.url)));
});

test('CSS uses only local assets and has narrow-screen / reduced-motion rules', async () => {
  const css = await readFile(join(root, 'style.css'), 'utf8');
  assert.doesNotMatch(css, /@import|url\s*\(|https?:/i);
  assert.match(css, /max-width:720px/);
  assert.match(css, /prefers-reduced-motion/);
});
test('case clearly identifies a historical proposal, not a merged change or fabricated run', () => {
  assert.match(contents['case.html'], /不是运行日志/);
  assert.match(contents['case.html'], /源码断言/);
  assert.match(contents['case.html'], /不是当前 master 已合入的证明/);
  assert.match(contents['case.html'], /state\.metadata\.has\("diff_review"\)/);
});
test('private / public deployment files separate content and ports', async () => {
  const conf = await readFile(new URL('../deploy/compose.yaml', import.meta.url), 'utf8');
  assert.match(conf, /127\.0\.0\.1:18080:8080/);
  assert.match(conf, /profiles: \[public\]/);
  assert.match(conf, /read_only: true/);
  assert.match(conf, /create_host_path: false/);
  assert.doesNotMatch(conf, /docker\.sock|AGENT_DATA_DIR|GITHUB_TOKEN|DEEPSEEK_API_KEY|\.\.\/\.\.\//);
  const nginx = await readFile(new URL('../deploy/nginx.conf', import.meta.url), 'utf8');
  assert.doesNotMatch(nginx, /proxy_pass|fastcgi_pass|autoindex on/);
  assert.match(nginx, /location \/ \{ return 404; \}/);
  assert.match(nginx, /script-src 'none'; connect-src 'none'/);
});
test('real local HTTP: allowlisted assets, HEAD, CSP, unknown paths and writes', async () => {
  const server = createServer();
  server.listen(0, '127.0.0.1');
  await once(server, 'listening');
  const base = `http://127.0.0.1:${server.address().port}`;
  try {
    for (const [path] of routes) {
      const response = await fetch(base + path);
      assert.equal(response.status, 200, path);
      assert.equal(response.headers.get('x-content-type-options'), 'nosniff');
      assert.match(response.headers.get('content-security-policy'), /connect-src 'none'/);
      assert.ok((await response.text()).length > 20);
    }
    const head = await fetch(base, { method: 'HEAD' });
    assert.equal(head.status, 200);
    assert.equal(await head.text(), '');
    for (const path of ['/tasks', '/tasks/x/events', '/ui-config', '/docs', '/.env', '/agent_tasks.sqlite3', '/deploy/compose.yaml', '/%2e%2e/README.md', '/unknown']) {
      assert.equal((await fetch(base + path)).status, 404, path);
    }
    for (const method of ['POST', 'PUT', 'DELETE', 'OPTIONS']) assert.equal((await fetch(base + '/tasks', { method })).status, 405);
  } finally {
    server.closeAllConnections();
    await new Promise((resolve) => server.close(resolve));
  }
});
