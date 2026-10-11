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

from smoke_env import REPO, _quiet, sandbox_env  # noqa: F402
from tai import cli as tai_cli  # noqa: E402

# The package name is the directory npm makes under node_modules, and the
# tests build their stand-in tree with the real name so _npm_configured's
# tail check is the production one.
PKG = "terminal-ai-helper"


def _sandbox_env(home: str, extra: dict | None = None) -> dict:
    return sandbox_env(home, extra)


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


def _fake_npm(fakebin: str, tree: str, bump: bool = True) -> None:
    """A `npm` that records its argv and bumps the tree's VERSION, as a real
    global install would — but runs no postinstall, which is the
    --ignore-scripts world the update path still has to survive. `bump=False`
    holds the version, which is how a scene reaches the "already up to date"
    branch with a real registry's answer."""
    bin_dir = pathlib.Path(fakebin)
    bin_dir.mkdir(parents=True, exist_ok=True)
    script = bin_dir / "npm"
    grow = (f'v="$(cat {tree}/VERSION)"; '
            f'echo "${{v%.*}}.$(( ${{v##*.}} + 1 ))" > {tree}/VERSION\n'
            if bump else "")
    script.write_text(
        "#!/usr/bin/env bash\n"
        'echo "npm argv: $*" >> "$NPM_LOG"\n'
        + grow +
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
    # or blocked-scripts world. The update goes to npm, and the installer
    # runs from the new tree so the user still ends up with the wrapper,
    # the rc lines and one welcome.
    done = _run_update(tree, env)
    assert done.returncode == 0, done.stderr
    assert ("npm argv: install -g terminal-ai-helper@latest"
            " --allow-scripts=terminal-ai-helper") in log.read_text()
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
    assert ("npm argv: install -g terminal-ai-helper@latest"
            " --allow-scripts=terminal-ai-helper") in log.read_text()
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

# The mixed-doors repair, and the report that made it visible. An install
# that switched doors left the rc line naming the tree the curl one-liner
# cloned: the wrapper pointed at the npm package, so `tai version` and
# `tai bench` reported a release the interactive shell was not running —
# and the shell kept paying for code npm's updates never touched. Two
# changes close it: the installer retargets a foreign `plugins/tai.*` line
# instead of reading it as "already enabled", and the update's rerun guard
# asks the wiring rather than the version, so an up-to-date-but-miswired
# install is repaired by the very command that said "already up to date".
with tempfile.TemporaryDirectory() as root:
    home = pathlib.Path(root) / "home"
    tree = _stage_npm_tree(root)
    fakebin = pathlib.Path(root) / "fakebin"
    _fake_npm(str(fakebin), tree, bump=False)
    log = pathlib.Path(root) / "npm.log"
    env = _sandbox_env(str(home), {
        "PATH": f"{fakebin}:{os.environ['PATH']}", "NPM_LOG": str(log)})

    # The old door's leavings: a checkout with both plugins, rc lines that
    # source it — one under the managed marker, one bare — and a wrapper
    # naming the npm tree, which is what made `tai version` lie.
    stale = home / ".tai" / "plugins"
    stale.mkdir(parents=True)
    for name in ("tai.zsh", "tai.bash"):
        (stale / name).write_text("# the old checkout's copy\n")
    (home / ".zshrc").write_text(
        "# tai autocomplete (installed by ./install.sh)\n"
        f"source {home}/.tai/plugins/tai.zsh\n")
    (home / ".bashrc").write_text(f"source {home}/.tai/plugins/tai.bash\n")
    wrapper = home / "bin"
    wrapper.mkdir()
    (wrapper / "tai").write_text(
        "#!/usr/bin/env bash\n"
        f"exec python3 -S -E {tree}/tai/cli.py \"$@\"\n")

    done = _run_update(tree, env)
    assert done.returncode == 0, done.stderr
    assert "✓ already up to date" in done.stdout, done.stdout
    assert "retargeted" in done.stdout, done.stdout
    assert done.stdout.count("the terminal that knows your next command") == 1
    text = (home / ".zshrc").read_text()
    assert f"source {tree}/plugins/tai.zsh" in text, text
    assert ".tai/plugins" not in text, text
    # The managed block keeps its place, line refreshed underneath it.
    assert "# tai autocomplete (installed by ./install.sh)" in text, text
    assert text.index("# tai autocomplete") < text.index(f"source {tree}"), text
    btext = (home / ".bashrc").read_text()
    assert f"source {tree}/plugins/tai.bash" in btext, btext
    assert ".tai/plugins" not in btext, btext
    # Exactly one line per shell: a retarget must not stack a second copy.
    assert text.count(f"source {tree}/plugins/tai.zsh") == 1, text
    assert btext.count(f"source {tree}/plugins/tai.bash") == 1, btext
    print("OK — update: an rc line naming the old door's tree is "
          "retargeted in place, marker and all.")

    # Converged: the second update finds the wiring it wrote and prints no
    # welcome over it.
    done = _run_update(tree, env)
    assert done.returncode == 0, done.stderr
    assert done.stdout.count("the terminal that knows your next command") == 0
    print("OK — update: the healed wiring converges, no welcome again.")

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

# --- `tai uninstall` on the npm door -----------------------------------------
#
# An uninstall that left the package itself installed would be the wrapper's
# complaint in reverse: the command gone, the files still on disk. The door is
# named twice — by the wrapper's own content, and by where the running cli.py
# lives — and either is enough, because each alone is true on a real machine.

with tempfile.TemporaryDirectory() as root:
    home = pathlib.Path(root) / "home"
    tree = _stage_npm_tree(root)
    fakebin = pathlib.Path(root) / "fakebin"
    _fake_npm(str(fakebin), tree)
    log = pathlib.Path(root) / "npm.log"
    wrapper_dir = pathlib.Path(home) / "bin"
    wrapper_dir.mkdir(parents=True)
    # The wrapper the postinstall writes, naming the tree npm unpacked.
    (wrapper_dir / "tai").write_text(
        "#!/usr/bin/env bash\n"
        f"exec python3 -S -E {tree}/tai/cli.py \"$@\"\n")
    env = _sandbox_env(str(home), {
        "PATH": f"{fakebin}:{os.environ['PATH']}", "NPM_LOG": str(log),
        "BIN_DIR": str(wrapper_dir)})
    # Run from the dev checkout — a git door by location — with the wrapper
    # naming the npm tree: the wrapper's content is the door signal here.
    done = subprocess.run([sys.executable, f"{REPO}/tai/cli.py", "uninstall"],
                          env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "rm -g terminal-ai-helper" in log.read_text(), log.read_text()
    assert not (wrapper_dir / "tai").exists(), "the wrapper went"
    assert "terminal-ai-helper (npm global package)" in done.stdout, done.stdout
    print("OK — uninstall: a wrapper that names the npm tree asks npm for "
          "the package back.")

with tempfile.TemporaryDirectory() as root:
    home = pathlib.Path(root) / "home"
    tree = _stage_npm_tree(root)
    fakebin = pathlib.Path(root) / "fakebin"
    _fake_npm(str(fakebin), tree)
    log = pathlib.Path(root) / "npm.log"
    env = _sandbox_env(str(home), {
        "PATH": f"{fakebin}:{os.environ['PATH']}", "NPM_LOG": str(log)})
    # No wrapper at all — the npm package is the only thing left — and the
    # process runs from the tree itself: _repo_dir is the door signal here,
    # which is how a user reaches uninstall after deleting the wrapper.
    done = subprocess.run([sys.executable, f"{tree}/tai/cli.py", "uninstall"],
                          env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "rm -g terminal-ai-helper" in log.read_text(), log.read_text()
    assert "tai removed" in done.stdout and "tai was not installed" not in done.stdout
    print("OK — uninstall from the npm tree itself removes the package too.")

    # No npm on PATH any more: the rest of the uninstall stands, and the
    # package is named with the command that removes it, not silently kept.
    env["PATH"] = "/nonexistent"
    done = subprocess.run([sys.executable, f"{tree}/tai/cli.py", "uninstall"],
                          env=env, capture_output=True, text=True, timeout=120)
    assert done.returncode == 0, done.stderr
    assert "npm rm -g terminal-ai-helper" in done.stdout, done.stdout
    print("OK — uninstall without npm says what removes the package.")
