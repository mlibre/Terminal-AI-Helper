#!/usr/bin/env node
'use strict';
// `tai` — the executable npm links for a global install.
//
// npm 11.16+ and 12 run install scripts only when they are allowed by name,
// so the postinstall hook that used to finish the job can no longer be the
// only door: `npm i -g terminal-ai-helper` with scripts blocked unpacked the
// package and linked nothing, and `tai` answered "command not found". A `bin`
// entry is different — npm links it whether or not any script ever runs — so
// this launcher is what the symlink points at.
//
// The program itself is one compiled binary; this file only finds it and
// hands the terminal over:
//   1. bin/tai — a binary the postinstall staged, or the npm door's fetch
//      left in the package,
//   2. bin/tai-linux-<arch>/tai — a release tarball extracted in place,
//   3. target/release/tai — a build beside a checkout of the repository,
//   4. ~/.local/bin/tai — where install.sh puts it for the shell plugins.
// First that exists wins. When none does — the scripts-blocked install, the
// common one now — the binary is fetched from the GitHub release for this
// machine's architecture, the same tarball the other doors download, and a
// first-run line points at the installer that wires the shell integration.

const { spawnSync } = require('child_process');
const fs = require('fs');
const os = require('os');
const path = require('path');

const pkgDir = path.resolve(__dirname, '..');

// The release tarballs are linux; the mapping is os.arch() -> the tarball's.
const archNames = { x64: 'x86_64', arm64: 'aarch64' };

function existing(p) {
  try {
    fs.accessSync(p, fs.constants.X_OK);
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

function candidates() {
  const list = [
    path.join(pkgDir, 'bin', 'tai'),
    path.join(pkgDir, 'target', 'release', 'tai'),
    path.join(os.homedir(), '.local', 'bin', 'tai'),
  ];
  const arch = archNames[os.arch()];
  if (arch) {
    list.splice(1, 0, path.join(pkgDir, 'bin', `tai-linux-${arch}`, 'tai'));
  }
  return list;
}

// Fetch the release tarball into bin/ and return the path of the binary it
// carried, or null. TAI_RELEASE_URL exists so a test can point this at a
// local tarball and run without a download.
function fetchBinary() {
  const arch = archNames[os.arch()];
  if (!arch || process.platform !== 'linux') return null;
  let version = 'latest';
  try {
    version = fs.readFileSync(path.join(pkgDir, 'VERSION'), 'utf8').trim() || 'latest';
  } catch {}
  const base = 'https://github.com/mlibre/Terminal-AI-Helper/releases';
  const url =
    process.env.TAI_RELEASE_URL ||
    (version === 'latest'
      ? `${base}/latest/download/tai-linux-${arch}.tar.gz`
      : `${base}/download/v${version}/tai-linux-${arch}.tar.gz`);

  const binDir = path.join(pkgDir, 'bin');
  const tarball = path.join(binDir, 'tai.tar.gz');
  const res = spawnSync(
    'bash',
    ['-c', `curl -fsSL -- "$1" -o "$2" 2>/dev/null || wget -qO "$2" "$1" 2>/dev/null`, 'tai-fetch', url, tarball],
    { stdio: ['ignore', 'ignore', 'ignore'] },
  );
  if (res.status !== 0 || !fs.existsSync(tarball)) return null;

  const extracted = spawnSync('tar', ['-xzf', tarball, '-C', binDir], { stdio: 'ignore' });
  fs.rmSync(tarball, { force: true });
  if (extracted.status !== 0) return null;

  // The tarball unpacks a directory — tai-linux-<arch>/tai; the flat shape is
  // accepted too, so a repacked tarball can never strand the launcher.
  const nested = path.join(binDir, `tai-linux-${arch}`, 'tai');
  const flat = path.join(binDir, 'tai');
  const found = existing(flat) ? flat : existing(nested) ? nested : null;
  if (found && found !== flat) {
    fs.renameSync(found, flat);
  }
  if (found) {
    try {
      fs.chmodSync(flat, 0o755);
    } catch {}
    return flat;
  }
  return null;
}

function main() {
  const args = process.argv.slice(2);
  const argv0 = 'tai';

  let bin = candidates().find(existing);
  let fetched = false;
  if (!bin) {
    bin = fetchBinary();
    fetched = !!bin;
  }
  if (!bin) {
    process.stderr.write(
      'tai: no tai binary in the package, and the release download failed.\n' +
        `     re-run: bash "${path.join(pkgDir, 'install.sh')}"\n`,
    );
    process.exit(1);
  }

  // The postinstall normally runs install.sh, which wires the plugins and
  // records the install method. When scripts were blocked that never happened,
  // so the first `tai` says where the wiring comes from — once, because the
  // record appears the moment any door completes it.
  if (fetched) {
    const method =
      process.env.XDG_DATA_HOME || path.join(os.homedir(), '.local', 'share');
    if (!fs.existsSync(path.join(method, 'tai', 'install-method'))) {
      process.stdout.write(
        `tai: fetched the binary — the shell plugins still need: bash "${path.join(pkgDir, 'install.sh')}"\n`,
      );
    }
  }

  const child = spawnSync(bin, args, { stdio: 'inherit', argv0 });
  if (child.error) {
    process.stderr.write(`tai: ${child.error.message}\n`);
    process.exit(127);
  }
  process.exit(child.status === null ? 128 + (child.signal ? 15 : 0) : child.status);
}

main();
