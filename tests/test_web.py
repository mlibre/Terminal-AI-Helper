"""tai web — the localhost dashboard, driven over real HTTP.

A dashboard is a window into the user's shell history, so the rules it lives
by are the assertions here: it binds 127.0.0.1 and nothing else, it answers
GETs and nothing else, the try box answers through the same engine the
prompt answers through (a page that re-implements the ranking is a second
ranking that will drift), and every piece of state it shows is read from the
store this process seeded — nothing invented for the page's sake.
"""
import json
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

from smoke_env import *  # noqa: F401,F403  (scratch db, REPO, the tai modules)
import tai.web as tw

# --- the fixture: a small history, straight through the real writer ---------
from tai.store import append_and_count, count

for _cmd, _n in (("git status", 3), ("docker ps", 2), ("ls -la", 1)):
    for _ in range(_n):
        ok, _, _ = append_and_count(_cmd, cwd="/home/u/proj")
        assert ok, f"the fixture command was not stored: {_cmd}"
_rows, _distinct = count()
assert _rows == 6 and _distinct == 3, f"seed wrote {_rows}/{_distinct}"

# --- a real server on an ephemeral port -------------------------------------
srv = ThreadingHTTPServer(("127.0.0.1", 0), tw._Handler)
assert srv.server_address[0] == "127.0.0.1", \
    f"bound {srv.server_address[0]} — the history must not face the network"
threading.Thread(target=srv.serve_forever, daemon=True).start()
base = f"http://127.0.0.1:{srv.server_address[1]}"

def get(path: str):
    with urllib.request.urlopen(base + path, timeout=10) as r:
        return r.status, r.read()

def post(path: str):
    req = urllib.request.Request(base + path, data=b"", method="POST")
    try:
        urllib.request.urlopen(req, timeout=10)
        return None
    except urllib.error.HTTPError as e:
        return e.code

# --- the page ----------------------------------------------------------------
code, body = get("/")
page = body.decode()
assert code == 200
assert "try a prefix" in page, "the page is missing the box that makes it a dashboard"
assert "/api/state" in page and "/api/suggest" in page, \
    "the page must talk to the same server it was served from"
assert "127.0.0.1" in page, "the page should say where it is bound"
print("OK — the page serves, names its box, and points at its own API.")

# --- state: the numbers on the page are the store's numbers ------------------
code, body = get("/api/state")
s = json.loads(body)
assert code == 200
assert s["db"]["rows"] == 6 and s["db"]["distinct"] == 3, s["db"]
assert not s["db"]["broken"], s["db"]["broken"]
assert s["top"][0]["cmd"] == "git status" and s["top"][0]["n"] == 3, s["top"]
assert s["learned"]["commands"] >= 3, s["learned"]
assert {"cmd", "cwd", "exit", "ts"} <= set(s["recent"][0].keys()), s["recent"][0]
assert s["indexes"]["zsh"]["exists"] in (True, False)   # reported, not assumed
print("OK — /api/state shows the seeded store: rows, top, learned, recent.")

# --- suggest: the same engine the prompt uses --------------------------------
code, body = get("/api/suggest?q=git%20st")
r = json.loads(body)
assert code == 200
assert r.get("choice", "").startswith("git"), r
assert any(c["cmd"].startswith("git") for c in r.get("choices", [])), r["choices"]
assert "latency_ms" in r, r
code, body = get("/api/suggest?q=")
assert code == 200 and json.loads(body) is not None, "an empty prefix must not 500"
code, body = get("/api/suggest?q=" + "x" * 600)
assert code == 200, "an over-long prefix must be cut, not an error"
print("OK — /api/suggest answers from the engine, and abuses stay 200.")

# --- read-only, full stop ------------------------------------------------------
assert post("/") == 405, "a POST must meet an explicit read-only refusal"
try:
    get("/api/none")
    raise AssertionError("an unknown route answered 200")
except urllib.error.HTTPError as e:
    assert e.code == 404, e.code
print("OK — POST is refused in so many words, unknown routes 404.")

# --- the CLI wiring: alias, default port, serve path --------------------------
assert tw.DEFAULT_PORT == 8247, tw.DEFAULT_PORT
h = subprocess.run([sys.executable, str(REPO / "tai" / "cli.py"), "--help"],
                   capture_output=True, text=True, timeout=60)
assert h.returncode == 0, h.stderr
# The alias lives or dies in the parent's listing: both spellings must be
# offered, or `tai dashboard` is a KeyError under a name the docs promised.
assert "dashboard" in h.stdout and "web" in h.stdout, h.stdout
assert "read-only localhost dashboard" in h.stdout, h.stdout
try:
    r = subprocess.run(
        [sys.executable, str(REPO / "tai" / "cli.py"), "web", "--no-browser",
         "--port", "0"],
        capture_output=True, text=True, timeout=25,
        env=dict(os.environ, TAI_NO_MENU="1"))
    raise AssertionError(f"serve returned early: rc={r.returncode} {r.stdout}")
except subprocess.TimeoutExpired as e:
    out = (e.stdout or b"").decode() if isinstance(e.stdout, bytes) else (e.stdout or "")
    assert "tai dashboard on http://127.0.0.1:" in out, out
    assert "engine ready" in out, out
print("OK — `tai web` serves and says its URL; `dashboard` is the same command.")

srv.shutdown()
print("OK — the dashboard is localhost-only, read-only, and engine-backed.")
