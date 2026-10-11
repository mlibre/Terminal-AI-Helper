"""Poll GitHub Actions for the pushed head commit until every run concludes.

The token comes from the remote URL — it is never printed, never written
anywhere. The poll ends the moment every run for the SHA is completed; a
single failure is reported with its name.

    python3 scripts/poll_tai_ci.py
"""
import json
import re
import subprocess
import sys
import time
import urllib.request

REPO = subprocess.run(["git", "remote", "get-url", "origin"],
                      capture_output=True, text=True, check=True).stdout.strip()
m = re.search(r"github\.com/([^/]+)/([^/.]+)", REPO)
OWNER, NAME = m.group(1), m.group(2)


def _credential_token() -> str:
    """The stored GitHub credential's password, by the same door git pushes
    through. Never printed, never written anywhere but this process's memory."""
    q = subprocess.run(
        ["git", "credential", "fill"],
        input="protocol=https\nhost=github.com\n\n",
        capture_output=True, text=True)
    got = re.search(r"^password=(.+)$", q.stdout, re.M)
    return got.group(1).strip() if got else ""


TOKEN = _credential_token()
HEAD = subprocess.run(["git", "rev-parse", "HEAD"],
                      capture_output=True, text=True, check=True).stdout.strip()


def api(path: str) -> dict:
    req = urllib.request.Request(
        f"https://api.github.com{path}",
        headers={"Accept": "application/vnd.github+json",
                 "Authorization": f"Bearer {TOKEN}" if TOKEN else "",
                 "User-Agent": "tai-ci-poll"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())


def main() -> int:
    print(f"polling Actions for {OWNER}/{NAME} @ {HEAD[:12]}")
    deadline = time.monotonic() + 540
    while time.monotonic() < deadline:
        runs = api(f"/repos/{OWNER}/{NAME}/actions/runs?head_sha={HEAD}").get(
            "workflow_runs", [])
        if runs:
            pending = [r for r in runs if r["status"] != "completed"]
            done = [(r["name"], r["conclusion"]) for r in runs
                    if r["status"] == "completed"]
            if not pending:
                failed = [n for n, c in done if c != "success"]
                for n, c in done:
                    print(f"  {n}: {c}")
                print("ALL-GREEN" if not failed else f"FAILED: {failed}")
                return 0 if not failed else 1
            print(f"  {len(pending)} run(s) in progress: "
                  + ", ".join(r["name"] for r in pending))
        else:
            print("  no runs for this SHA yet")
        time.sleep(15)
    print("TIMEOUT waiting for CI")
    return 2


if __name__ == "__main__":
    sys.exit(main())
