"""Real-scale bench in a sandbox HOME: 7.6k zsh history rows, giant lines included.

The measured promises (index build, store load, suggest latency, per-shell
startup index cost) are answered at the scale the user's machine actually has,
never against the developer's — and never against the real HOME, which the
probe only creates files under a throwaway path it deletes afterwards.

    python3 scripts/bench_sandbox.py
"""
import os
import pathlib
import random
import shutil
import subprocess
import sys
import time

REPO = pathlib.Path(__file__).resolve().parent.parent
HOME = pathlib.Path("/tmp/tai/tai_bench_home")
ROWS = 7587

# The vocabulary the synthetic history draws from: a core of everyday verbs
# with realistic arguments, a set of project-local pairs (so the sequence
# table has keys with several followers), and the multi-KB one-liners the
# user's own history carries.
CORE = [
    "git status", "git pull --rebase", "git push origin master", "git add -A",
    "git commit -m 'wip'", "ls -la", "ls -lh", "cd ..", "cd ~/src",
    "docker compose up -d", "docker ps", "nvim .zshrc", "cat notes.txt",
    "systemctl stop cups.path", "sudo systemctl stop cups.path",
    "rm /tmp/plasma-csd-generator.Athsxt/", "rm -r /tmp/plasma-csd-generator.Athsxt/",
    "python3 -m pytest -x -q", "make build", "tai refresh",
]
PAIRS = [(f"cd ~/tmp/fun-game/proj-{i}", "make build")
         for i in range(40)]
GIANT = ("curl -sSL 'https://example.com/install.sh' -o /tmp/x.sh && "
         "sha256sum /tmp/x.sh && " + "echo padding-" + "x" * 1200 + " | tee /dev/null")


def write_history(path: pathlib.Path) -> None:
    rng = random.Random(37)
    lines = []
    ts = 1_700_000_000
    for i in range(ROWS):
        ts += rng.randint(30, 600)
        if i % 500 == 499:
            cmd = GIANT
        elif i % 7 == 0:
            prev, nxt = PAIRS[rng.randrange(len(PAIRS))]
            lines.append(f": {ts}:0;{prev}")
            ts += rng.randint(5, 60)
            cmd = nxt
        else:
            cmd = CORE[rng.randrange(len(CORE))]
        lines.append(f": {ts}:0;{cmd}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main() -> int:
    shutil.rmtree(HOME, ignore_errors=True)
    (HOME / ".local" / "share" / "tai").mkdir(parents=True)
    write_history(HOME / ".zsh_history")
    env = dict(os.environ, HOME=str(HOME))
    env.pop("TAI_DB", None)
    env.pop("TAI_INDEX", None)
    tai = [sys.executable, str(REPO / "tai" / "cli.py")]

    t0 = time.time()
    got = subprocess.run(tai + ["refresh"], env=env, capture_output=True, text=True)
    build_s = time.time() - t0
    print(got.stdout.strip() or got.stderr.strip())
    print(f"index build: {build_s:.2f}s ({ROWS} rows)")

    out = subprocess.run(tai + ["bench"], env=env, capture_output=True, text=True)
    print(out.stdout.strip() or out.stderr.strip())

    # The prophecy question, asked at the sandbox scale: suggest with an empty
    # prefix and the last command the history ends on as the sequence key.
    last = "make build"
    out = subprocess.run(
        tai + ["suggest", "--cwd", str(HOME), "--last", last],
        env=env, capture_output=True, text=True)
    print("empty-prefix suggest:", (out.stdout or out.stderr).strip()[:200])

    # Per-shell index source cost, measured the way the plugins read it. The
    # bundled zsh is the floor, the same one the pty suite stands on.
    zsh = shutil.which("zsh")
    if not zsh:
        bundled = REPO / "tests" / "bin" / "zsh"
        zsh = str(bundled) if bundled.is_file() and os.access(bundled, os.X_OK) else None
    idx = HOME / ".local/share/tai/zsh-index.zsh"
    if zsh and idx.exists():
        t0 = time.time()
        subprocess.run([zsh, "-fc", f"source {idx}"], env=env, check=True)
        print(f"zsh index source: {(time.time() - t0) * 1000:.0f}ms "
              f"({idx.stat().st_size // 1024}kB)")
    shutil.rmtree(HOME, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
