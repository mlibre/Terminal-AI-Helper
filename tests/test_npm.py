"""The npm door: one `npm i -g` installs tai, and `tai update` knows its own.

    python3 tests/test_npm.py

npm is the third install door beside the checkout and the curl one-liner, so
its two halves both have to hold:

* the postinstall hook runs the installer for a *global* install only — a dev
  `npm i` in a checkout of this repo must never touch anyone's rc files — and
  an installer that cannot finish must not fail the npm install over it;
* `tai update` from a tree npm installed goes back to npm for the new
  version, and reruns install.sh only when the postinstall hook did not
  already (the --ignore-scripts case), or the user watches the welcome twice.

Every scenario runs in a throwaway HOME through subprocesses or a fake `npm`
on PATH — never against the developer's own shell, and never against the
network.
"""
import contextlib
import io
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

from smoke_env import REPO, _quiet  # noqa: F402
from tai import cli as tai_cli  # noqa: E402

# The package name is the directory npm makes under node_modules, and the
# tests build their stand-in tree with the real name so _npm_configured's
# tail check is the production one.
PKG = "terminal-ai-helper"


def _sandbox_env(home: str, extra: dict | None = None) -> dict:
    env = dict(os.environ, HOME=home, XDG_DATA_HOME=f"{home}/share",
               BIN_DIR=f"{home}/bin", TAI_DB=f"{home}/history.db",
               TAI_INDEX=f"{home}/zsh-index.zsh", HISTFILE="",
               TAI_HISTORY_FILES="")
    env.update(extra or {})
    return env


def _stage_npm_tree(root: str) -> str:
    """A stand-in for what npm unpacks: the product files under node_modules."""
    tree = pathlib.Path(root) / "prefix/lib/node_modules" / PKG
    (tree / "tai").mkdir(parents=True)
    shutil.copytree(f"{REPO}/plugins", tree / "plugins")
    shutil.copy2(f"{REPO}/install.sh", tree / "install.sh")
    shutil.copy2(f"{REPO}/VERSION", tree / "VERSION")
    for py in pathlib.Path(f"{REPO}/tai").glob("*.py"):
        shutil.copy2(py, tree / "tai" / py.name)
    return str(tree)


def _fake_npm(fakebin: str, tree: str) -> None:
    """A `npm` that records its argv and bumps the tree's VERSION, as a real
    global install would — but runs no postinstall, which is the
    --ignore-scripts world the update path still has to survive."""
    bin_dir = pathlib.Path(fakebin)
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "npm"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'echo "npm argv: $*" >> "$NPM_LOG"\n'
        f'v="$(cat {tree}/VERSION)"; '
        f'echo "${{v%.*}}.$(( ${{v##*.}} + 1 ))" > {tree}/VERSION\n'
        "exit 0\n")
    script.chmod(0o755)


def _run_update(tree: str, env: dict) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, f"{tree}/tai/cli.py", "update"],
                          env=env, capture_output=True, text=True, timeout=300)


# --- the postinstall hook ---------------------------------------------------

with tempfile.TemporaryDirectory() as home:
    # A dev `npm i` in this checkout is not a global install: the hook exits
    # before anything is asked of the machine, and the HOME stays empty.
    done = subprocess.run(
        ["bash", f"{REPO}/bin/npm-postinstall"],
        env=_sandbox_env(home, {"npm_config_global": ""}),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0, done.stderr
    assert list(pathlib.Path(home).iterdir()) == [], sorted(os.listdir(home))
    print("OK — postinstall: a non-global install is a no-op.")

    # The same script under npm's global flag finishes the install itself:
    # both rc files and the wrapper, and the welcome the other doors print.
    done = subprocess.run(
        ["bash", f"{REPO}/bin/npm-postinstall"],
        env=_sandbox_env(home, {"npm_config_global": "true"}),
        capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.stderr
    assert "✓ zsh installed" in done.stdout and "✓ bash installed" in done.stdout
    assert "the terminal that knows your next command" in done.stdout
    wrapper = pathlib.Path(home) / "bin" / "tai"
    assert wrapper.exists(), "the postinstall did not write the wrapper"
    assert f"{REPO}/tai/cli.py" in wrapper.read_text()
    for rc, plugin in ((".zshrc", "tai.zsh"), (".bashrc", "tai.bash")):
        text = (pathlib.Path(home) / rc).read_text()
        assert f"plugins/{plugin}" in text, rc
    print("OK — postinstall: a global install is the whole install.")

    # The opt-out for scripts and containers: files only, nothing configured.
    for rc in (".zshrc", ".bashrc"):
        (pathlib.Path(home) / rc).unlink()
    wrapper.unlink()
    done = subprocess.run(
        ["bash", f"{REPO}/bin/npm-postinstall"],
        env=_sandbox_env(home, {"npm_config_global": "true",
                                "TAI_SKIP_POSTINSTALL": "1"}),
        capture_output=True, text=True, timeout=60)
    assert done.returncode == 0 and done.stdout == "", done.stdout
    assert not wrapper.exists() and not (pathlib.Path(home) / ".zshrc").exists()
    print("OK — postinstall: TAI_SKIP_POSTINSTALL=1 skips the setup.")

    # An installer that cannot finish must not roll the package back: the
    # hint is printed, the exit stays 0, and npm keeps the files on disk.
    # Both rc paths are directories here, so no shell can be enabled.
    for rc in (".zshrc", ".bashrc"):
        (pathlib.Path(home) / rc).unlink(missing_ok=True)
        (pathlib.Path(home) / rc).mkdir()
    done = subprocess.run(
        ["bash", f"{REPO}/bin/npm-postinstall"],
        env=_sandbox_env(home, {"npm_config_global": "true"}),
        capture_output=True, text=True, timeout=300)
    assert done.returncode == 0, done.returncode
    assert f"{REPO}/install.sh" in done.stderr, done.stderr
    print("OK — postinstall: a failed setup stays installed and says so.")

# --- _install_method, the routing decision ----------------------------------

assert tai_cli._install_method(
    pathlib.Path("/x/node_modules/" + PKG)) == "npm"
assert tai_cli._install_method(pathlib.Path(REPO)) == "git", \
    "this suite runs from a checkout"
with tempfile.TemporaryDirectory() as plain:
    assert tai_cli._install_method(pathlib.Path(plain)) == "none", \
        "a .deb tree is neither npm's nor a checkout's"
print("OK — _install_method: npm tree, checkout, and the .deb in between.")

# --- `tai update` from an npm tree ------------------------------------------

with tempfile.TemporaryDirectory() as root:
    home = pathlib.Path(root) / "home"
    tree = _stage_npm_tree(root)
    fakebin = pathlib.Path(root) / "fakebin"
    _fake_npm(str(fakebin), tree)
    log = pathlib.Path(root) / "npm.log"
    env = _sandbox_env(str(home), {
        "PATH": f"{fakebin}:{os.environ['PATH']}", "NPM_LOG": str(log)})

    # The version changed and nothing is configured — the --ignore-scripts
    # world. The update goes to npm, and the installer runs from the new tree
    # so the user still ends up with the wrapper, the rc lines and one welcome.
    done = _run_update(tree, env)
    assert done.returncode == 0, done.stderr
    assert "npm argv: install -g terminal-ai-helper@latest" in log.read_text()
    assert "✓ updated to" in done.stdout, done.stdout
    assert done.stdout.count("the terminal that knows your next command") == 1
    assert f"{tree}/tai/cli.py" in (home / "bin" / "tai").read_text()
    for rc, plugin in ((".zshrc", "tai.zsh"), (".bashrc", "tai.bash")):
        assert f"plugins/{plugin}" in (home / rc).read_text(), rc

    # The second update finds everything the installer just wrote, and does
    # not print the welcome a second time over it.
    log.write_text("")
    done = _run_update(tree, env)
    assert done.returncode == 0, done.stderr
    assert "npm argv: install -g terminal-ai-helper@latest" in log.read_text()
    assert "✓ updated to" in done.stdout, done.stdout
    assert done.stdout.count("the terminal that knows your next command") == 0
    print("OK — update from an npm tree: npm answers, "
          "the installer runs once, not twice.")

    # The one-way door: with no npm there is nothing to update from, and the
    # message names the command that works instead.
    env.pop("NPM_LOG")
    env["PATH"] = "/nonexistent"
    done = _run_update(tree, env)
    assert done.returncode == 1, done.returncode
    assert "npm is not installed" in done.stdout
    assert "npm i -g terminal-ai-helper@latest" in done.stdout
    print("OK — update without npm says what to do instead.")

# In-process, because the no-npm answer is one `which` away and a subprocess
# with a gutted PATH could not run the installer it must not reach. The repo
# is pointed at a staged node_modules tree — the real one would take the git
# door — and the hook is patched, the way the non-repo case above works.
real_which = shutil.which
real_repo_dir = tai_cli._repo_dir
staged = _stage_npm_tree(tempfile.mkdtemp())
try:
    tai_cli._repo_dir = lambda: pathlib.Path(staged)
    shutil.which = lambda name, *a, **k: None if name == "npm" else real_which(name, *a, **k)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = tai_cli.cmd_update(_quiet(False))
finally:
    shutil.which = real_which
    tai_cli._repo_dir = real_repo_dir
    shutil.rmtree(pathlib.Path(staged).parent.parent.parent)
assert code == 1 and "npm is not installed" in out.getvalue(), out.getvalue()
print("OK — update: no npm is answered in one `which`, before anything runs.")
