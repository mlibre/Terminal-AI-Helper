"""The .deb: what it carries, and that it is the compiled program.

    python3 tests/test_deb.py

`scripts/build_deb.sh` compiles the release binary and packages it with the
shell plugins. The package is architecture-keyed now, not "all": the binary
inside is the machine this built on. dpkg-deb is the floor; without it the
suite says so and passes, the way the pty suite treats a missing zsh.
"""
import pathlib
import shutil
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
VERSION = (REPO / "VERSION").read_text().strip()

failures: list[str] = []


def check(what: str, got, want) -> None:
    if got == want:
        print(f"  ok   {what}: {got!r}")
    else:
        print(f"  FAIL {what}:\n         got  {got!r}\n         want {want!r}")
        failures.append(what)


if not shutil.which("dpkg-deb"):
    print("dpkg-deb is not installed — the deb checks are skipped")
    sys.exit(0)

got = subprocess.run(["bash", str(REPO / "scripts" / "build_deb.sh")],
                     cwd=REPO, capture_output=True, text=True, timeout=900)
check("build exits 0", got.returncode, 0)
arch = subprocess.run(["dpkg", "--print-architecture"], capture_output=True,
                      text=True).stdout.strip() or "amd64"
deb = REPO / "dist" / f"tai_{VERSION}_{arch}.deb"
check("the deb is named for version and architecture", deb.exists(), True)

listing = subprocess.run(["dpkg-deb", "-c", str(deb)], capture_output=True,
                         text=True).stdout
paths = {line.split(maxsplit=5)[-1].removeprefix("./") for line in listing.splitlines() if line.strip()}
check("the binary is /usr/bin/tai", "usr/bin/tai" in paths, True)
check("the zsh plugin ships", "usr/lib/tai/plugins/tai.zsh" in paths, True)
check("the bash plugin ships", "usr/lib/tai/plugins/tai.bash" in paths, True)
check("no Python ships", not any(p.endswith(".py") for p in paths), True)

control = subprocess.run(["dpkg-deb", "-f", str(deb), "Architecture"],
                         capture_output=True, text=True).stdout.strip()
check("architecture is the build machine's", control, arch)
depends = subprocess.run(["dpkg-deb", "-f", str(deb), "Depends"],
                         capture_output=True, text=True).stdout
check("no python3 dependency", "python3" not in depends, True)

# The binary inside runs, and says the release it is.
extract = pathlib.Path(f"/tmp/tai/tai_deb_{VERSION}")
shutil.rmtree(extract, ignore_errors=True)
extract.mkdir(parents=True)
subprocess.run(["dpkg-deb", "-x", str(deb), str(extract)], check=True)
got = subprocess.run([str(extract / "usr" / "bin" / "tai"), "version"],
                     capture_output=True, text=True, timeout=60,
                     env={"HOME": str(extract)})
check("the packaged binary answers", got.stdout.startswith(f"tai {VERSION}"), True)
shutil.rmtree(extract, ignore_errors=True)

print()
if failures:
    print(f"FAILED ({len(failures)}) — fix and re-run")
    for f in failures:
        print("  -", f)
    sys.exit(1)
print("OK — the deb carries the compiled program and the shell integration.")
