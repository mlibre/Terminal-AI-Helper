"""The npm door: the package npm ships, and what its postinstall does.

    python3 tests/test_npm.py

The registry package is architecture-neutral: plugins, installer, version —
and the postinstall fetches or builds the binary. Both facts are checked here
against a real `npm pack`, and the hook is run twice: once with the scripts
skip (containers want files on disk only), once with the binary pre-staged —
the way a platform package or a previous run would have left it — so the full
npm install runs hermetically, without a download.

node and npm are the floor this suite stands on; without them it says so and
passes, the way the pty suite treats a missing zsh.
"""
import json
import os
import pathlib
import platform
import shutil
import subprocess
import sys
import tarfile

REPO = pathlib.Path(__file__).resolve().parent.parent
VERSION = (REPO / "VERSION").read_text().strip()

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


def have(cmd: str) -> bool:
    return shutil.which(cmd) is not None


if not (have("npm") and have("node")):
    print("npm and node are not installed — the package checks are skipped")
    sys.exit(0)

pack_dir = pathlib.Path(f"/tmp/tai/tai_npm_{os.getpid()}")
shutil.rmtree(pack_dir, ignore_errors=True)
pack_dir.mkdir(parents=True)

# -- what the package ships ---------------------------------------------------

packed = subprocess.run(
    ["npm", "pack", "--pack-destination", str(pack_dir)],
    cwd=REPO, capture_output=True, text=True, timeout=300,
)
check("npm pack exits 0", packed.returncode, 0)
tarball = pack_dir / f"terminal-ai-helper-{VERSION}.tgz"
check("the tarball is named for the version", tarball.exists(), True)

with tarfile.open(tarball) as tf:
    names = tf.getnames()
    tf.extractall(pack_dir / "pkg")

pkg = pack_dir / "pkg" / "package"
files = {n.removeprefix("package/") for n in names}
check("the plugins ship", {"plugins/tai.zsh", "plugins/tai.bash"} <= files, True)
check("the plugin parts ship",
      any(n.startswith("plugins/zsh/") for n in files)
      and any(n.startswith("plugins/bash/") for n in files), True)
check("the installer ships", "install.sh" in files, True)
check("the postinstall hook ships", "bin/npm-postinstall" in files, True)
check("the version file ships", "VERSION" in files, True)
check("no Python ships", not any(n.endswith(".py") for n in files), True)
check("no __pycache__ ships", not any("__pycache__" in n for n in files), True)

pkg_json = json.loads((pkg / "package.json").read_text())
check("package version matches VERSION", pkg_json["version"], VERSION)

# -- the guards ---------------------------------------------------------------

env = dict(os.environ, HOME=str(pack_dir / "home"), XDG_DATA_HOME=str(pack_dir / "data"))
got = subprocess.run(["bash", str(pkg / "bin" / "npm-postinstall")],
                     env=env, capture_output=True, text=True, timeout=120)
check("a non-global install is skipped", got.returncode, 0)
check("it said nothing", got.stdout, "")

got = subprocess.run(["bash", str(pkg / "bin" / "npm-postinstall")],
                     env=dict(env, npm_config_global="1", TAI_SKIP_POSTINSTALL="1"),
                     capture_output=True, text=True, timeout=120)
check("TAI_SKIP_POSTINSTALL skips it", got.returncode, 0)
check("it wrote no rc file", (pack_dir / "home" / ".zshrc").exists(), False)

# -- the whole door, with the binary staged -----------------------------------

bin_dir = pack_dir / "home" / ".local" / "bin"
bin_dir.mkdir(parents=True)
built = REPO / "target" / "release" / "tai"
if not built.exists():
    built = REPO / "target" / "debug" / "tai"
shutil.copy(built, pkg / "bin" / "tai")
(pkg / "bin" / "tai").chmod(0o755)
got = subprocess.run(
    ["bash", str(pkg / "bin" / "npm-postinstall")],
    env=dict(env, npm_config_global="1",
             BIN_DIR=str(bin_dir),
             PATH=str(pkg / "bin") + ":" + os.environ["PATH"],
             ZDOTDIR=str(pack_dir / "home")),
    capture_output=True, text=True, timeout=300,
)
check("the postinstall exits 0", got.returncode, 0)
check("the install ran", "✓ zsh installed" in got.stdout, True)
zshrc = (pack_dir / "home" / ".zshrc").read_text()
check("the rc points into the package",
      f"{pkg}/plugins/tai.zsh" in zshrc.replace("\\ ", " ") or "plugins/tai.zsh" in zshrc, True)
method = pack_dir / "data" / "tai" / "install-method"
check("the npm door is recorded", method.exists() and method.read_text().strip() == "npm", True)
got = subprocess.run([str(bin_dir / "tai"), "version"],
                     env=env, capture_output=True, text=True, timeout=60)
check("the wrapper answers", got.stdout.startswith(f"tai {VERSION}"), True)

print()
if failures:
    print(f"FAILED ({len(failures)}) — fix and re-run")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK — the package ships the shell integration and the hook finishes the install.")
