"""The .deb package: one build, then the assertions.

    python3 tests/test_deb.py

dpkg-deb is present on every Debian-family machine — which is the only place a
.deb matters — and the machine that runs this suite does not need root: the
package is built with --root-owner-group and inspected by extraction, never by
installation. What the assertions buy is a release that cannot ship a package
whose wrapper or plugin tree is broken, because CI builds it on every push and
attaches the artifact to the release.
"""
import os
import pathlib
import shutil
import subprocess
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
from smoke_env import REPO  # noqa: E402  (repo root + the tai modules on sys.path)


def main() -> int:
    if not shutil.which("dpkg-deb"):
        print("SKIP — dpkg-deb is not on this machine; the .deb ships from CI.")
        return 0
    version = (REPO / "VERSION").read_text(encoding="utf-8").strip()
    out = tempfile.mkdtemp(prefix="tai-deb-")
    stage = tempfile.mkdtemp(prefix="tai-deb-inspect-")
    try:
        built = subprocess.run(["scripts/build_deb.sh"], cwd=REPO,
                               env=dict(os.environ, TAI_DEB_OUT=out),
                               capture_output=True, text=True, timeout=300)
        # The script writes next to the checkout; the suite copies nothing,
        # it only asserts the artifact exists for the right version.
        pkg = REPO / "dist" / f"tai_{version}_all.deb"
        assert built.returncode == 0, built.stderr
        assert pkg.exists(), f"the build did not produce {pkg}"
        info = subprocess.run(["dpkg-deb", "-I", str(pkg)],
                              capture_output=True, text=True, check=True).stdout
        assert f"Version: {version}" in info, info
        assert "Package: tai" in info and "Architecture: all" in info, info
        # Extract (no root needed) and run the packaged CLI for real.
        subprocess.run(["dpkg-deb", "-x", str(pkg), stage], check=True)
        root = pathlib.Path(stage)
        cli = root / "usr/lib/tai/tai/cli.py"
        assert cli.is_file(), "the Python package is missing from the package"
        wrapper = (root / "usr/bin/tai").read_text()
        assert "python3 -S -E /usr/lib/tai/tai/cli.py" in wrapper, wrapper
        env = dict(os.environ, TAI_DB=str(root / "scratch.db"))
        got = subprocess.run([sys.executable, str(cli), "version"], env=env,
                             capture_output=True, text=True, timeout=60)
        assert got.stdout.strip() == f"tai {version}", got.stdout
        # Both plugins are present and parse, since the shell integration is
        # what a package install is actually for.
        zsh = REPO / "tests" / "bin" / "zsh"
        if zsh.is_file() and os.access(zsh, os.X_OK):
            check = subprocess.run(
                [str(zsh), "-f", "-c",
                 f"source {root}/usr/lib/tai/plugins/tai.zsh && print ZSH-PLUGIN-OK"],
                env=dict(env, FPATH=str(REPO / "tests/bin/zsh-functions")),
                capture_output=True, text=True, timeout=60)
            assert "ZSH-PLUGIN-OK" in check.stdout, check.stderr
        bash = subprocess.run(
            ["bash", "-c",
             f"source {root}/usr/lib/tai/plugins/tai.bash && echo BASH-PLUGIN-OK"],
            env=env, capture_output=True, text=True, timeout=60)
        assert "BASH-PLUGIN-OK" in bash.stdout, bash.stderr
        print(f"OK — the .deb builds, installs its tree, and runs: tai_{version}_all.deb")
    finally:
        shutil.rmtree(out, ignore_errors=True)
        shutil.rmtree(stage, ignore_errors=True)
        shutil.rmtree(REPO / "dist", ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
