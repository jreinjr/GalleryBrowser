// Loads ANTHROPIC_API_KEY (and friends) from .env at the repo root, or in a
// git worktree from the main checkout's .env, without overriding the
// environment. Returns the file used, or null.
import fs from 'node:fs';
import path from 'node:path';
import { execSync } from 'node:child_process';

export function loadEnv(root) {
  const candidates = [path.join(root, '.env')];
  try {
    const common = execSync('git rev-parse --git-common-dir', { cwd: root, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] }).trim();
    candidates.push(path.join(path.resolve(root, common), '..', '.env'));
  } catch (e) { /* not a git checkout */ }
  for (const f of candidates) {
    if (!fs.existsSync(f)) continue;
    for (const line of fs.readFileSync(f, 'utf8').split('\n')) {
      const m = /^([A-Z_][A-Z0-9_]*)=(.*)$/.exec(line.trim());
      if (m && !(m[1] in process.env)) process.env[m[1]] = m[2].replace(/^["']|["']$/g, '');
    }
    return f;
  }
  return null;
}

// Parses a whole SSE body into [{event, data}].
export function parseSSE(text) {
  const out = [];
  for (const block of text.split('\n\n')) {
    let event = 'message', data = '';
    for (const line of block.split('\n')) {
      if (line.startsWith('event:')) event = line.slice(6).trim();
      else if (line.startsWith('data:')) data += line.slice(5).trim();
    }
    if (!data) continue;
    try { out.push({ event, data: JSON.parse(data) }); } catch (e) { out.push({ event, data }); }
  }
  return out;
}
