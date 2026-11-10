// Repository layout guard: the web app and its generated client have one owner path.
import assert from 'node:assert/strict';
import { execFileSync } from 'node:child_process';
import { readFileSync, existsSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const root = fileURLToPath(new URL('../', import.meta.url));
const web = new URL('../apps/web/', import.meta.url);
const tracked = execFileSync('git', ['ls-files', '-z'], { cwd: root, encoding: 'utf8' })
  .split('\0').filter(Boolean);
assert(!tracked.some(path => path.startsWith('frontend/')), 'Tracked frontend/ must not contain a second web app');
assert(!tracked.some(path => /^apps\/web\/(node_modules|dist|test-results|playwright-report)\//.test(path)), 'Generated web outputs must stay untracked');
for (const path of ['package.json', 'package-lock.json', 'index.html', 'vite.config.ts', 'tsconfig.json', 'src/main.tsx', 'e2e/api.spec.ts']) {
  assert(existsSync(new URL(path, web)), `Missing web application file: ${path}`);
}
const manifest = JSON.parse(readFileSync(new URL('package.json', web), 'utf8'));
const lock = JSON.parse(readFileSync(new URL('package-lock.json', web), 'utf8'));
assert.equal(lock.name, manifest.name, 'Web manifest and lock package names differ');
for (const field of ['dependencies', 'devDependencies']) {
  assert.deepEqual(lock.packages[''][field], manifest[field], `Web lock differs from ${field}`);
}
const workflow = readFileSync(new URL('../.github/workflows/environment.yml', import.meta.url), 'utf8');
assert(workflow.includes('cache-dependency-path: apps/web/package-lock.json'), 'CI must cache the active web lock');
assert(workflow.includes('working-directory: apps/web'), 'CI must build the active web application');
assert(!/\bfrontend\//.test(workflow), 'CI must not use the previous app directory');
// Run from repository root: code generation must resolve OpenAPI relative to the script.
execFileSync(process.execPath, [fileURLToPath(new URL('scripts/generate-api.mjs', web)), '--check'], {
  cwd: root, stdio: 'inherit',
});
console.log('Web layout valid: apps/web, matching npm lock, canonical OpenAPI and CI paths');
