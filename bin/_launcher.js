'use strict';
// Resolution du runtime Python pour les wrappers npm d'IcyCache.
//
// Un client MCP stdio possede stdout : rien ne doit y etre ecrit par le
// wrapper. Tous les diagnostics partent sur stderr.

const { spawn, spawnSync } = require('node:child_process');

const REPO = 'git+https://github.com/MlkBlk-IT/icycache.git';

function which(cmd) {
  const probe = process.platform === 'win32' ? 'where' : 'which';
  const r = spawnSync(probe, [cmd], { encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] });
  if (r.status !== 0) return null;
  const first = (r.stdout || '').split(/\r?\n/).filter(Boolean)[0];
  return first || null;
}

// Candidats, du plus specifique au plus general.
function candidates(args) {
  const list = [];

  // 1. IcyCache deja installe (pip / uv) -> script console.
  const installed = which('icycache');
  if (installed) list.push({ cmd: installed, args: args.slice() });

  // 2. uv provisionne un env isole depuis le depot GitHub.
  const uv = which('uv');
  if (uv) {
    list.push({ cmd: uv, args: ['tool', 'run', '--from', REPO, 'icycache', ...args] });
  }

  // 3. Python ou le paquet est importable.
  for (const py of ['python3', 'python']) {
    const p = which(py);
    if (p) list.push({ cmd: p, args: ['-m', 'icycache', ...args] });
  }

  return list;
}

function run(args) {
  const list = candidates(args);
  if (list.length === 0) {
    process.stderr.write(
      '[icycache] no Python runtime found. Install Python 3.10+ and `pip install icycache`,\n' +
      '[icycache] or install uv (https://docs.astral.sh/uv/) which this wrapper uses to\n' +
      '[icycache] provision an isolated environment from GitHub.\n'
    );
    process.exit(127);
  }

  let lastError = null;
  for (const { cmd, args: cmdArgs } of list) {
    try {
      const child = spawn(cmd, cmdArgs, { stdio: 'inherit' });
      child.on('error', (err) => { lastError = err; });
      child.on('exit', (code, signal) => {
        if (signal) process.kill(process.pid, signal);
        else process.exit(code === null ? 1 : code);
      });
      return 0;
    } catch (err) {
      lastError = err;
    }
  }

  process.stderr.write(`[icycache] failed to start: ${lastError && lastError.message}\n`);
  process.exit(1);
}

module.exports = { run, which };
