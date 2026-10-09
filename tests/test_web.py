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
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

from smoke_env import *  # noqa: F401,F403  (scratch db, REPO, the tai modules)
import tai.web as tw

# --- the fixture: a small history, straight through the real writer ---------
from tai.store import append_and_count, count, session

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
assert "data-theme" in page and "localStorage" in page, \
    "the page must carry the light/dark toggle and remember the choice"
assert 'id="theme"' in page and 'id="toast"' in page, \
    "the friendly bits — theme button, copy toast — ship with the page"
assert 'id="net"' in page, \
    "a failed poll must show the reconnect banner, not blank panels"
assert 'localStorage.setItem("tai-theme"' in page, \
    "the theme choice is persisted under one name, not just flipped"
assert "button" in page and 'class="why"' in page, \
    "every suggestion row carries the why toggle that opens its arithmetic"
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

# --- the history's monsters arrive bounded -------------------------------------
# Real stores carry rows from before the recorder rejected multi-kilobyte and
# multi-line input — pasted JSON blobs, heredoc fragments. Written straight to
# the store the way an old import leaves them, they must reach the page as one
# bounded line: the table they render in stays a table, and the state answer
# stays small enough to fetch (a user saw NetworkError where `state` should
# have been, on rows exactly like these).
monster = '{"content": "' + "x" * 6000 + '"\nsecond "line"\n\tend}'
with session() as con:
    con.execute("INSERT INTO commands(cmd, cwd, exit_code, ts) VALUES(?,?,0,?)",
                (monster, "/home/u/proj\nsecond dir", int(time.time())))
    con.commit()          # the raw insert mimics an old database, not the recorder
code, body = get("/api/state")
s = json.loads(body)
assert code == 200
assert len(body) < 100_000, f"the state answer must stay fetchable, was {len(body)}"
assert all("\n" not in r["cmd"] and "\t" not in r["cmd"]
           for r in s["recent"] + s["top"]), "one line, always"
assert all("\n" not in r["cwd"] and len(r["cwd"]) <= 81 for r in s["recent"]), \
    "cwd collapses and bounds too"
assert all(len(r["cmd"]) <= 121 for r in s["recent"] + s["top"]), \
    s["recent"][0]["cmd"][:200]
assert any(r["cmd"].endswith("…") for r in s["recent"]), \
    "the giant row is clamped, not dropped"
assert s["top"][0]["cmd"] == "git status" and s["top"][0]["n"] == 3, \
    "clamping never reorders"
print("OK — monster history rows arrive as one bounded line; the answer stays small.")

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

# --- explain: why one suggestion scores what it scores -----------------------
# The panel says `git status` is 39% and the user asks why. The route answers
# with the factors the scoring loop added, and the one number that must agree
# is the total: explain is a twin of the hot loop, not a shared function, so
# this pin is what keeps the twins from drifting — for every candidate the
# panel offers, explain's total is the panel's score.
code, body = get("/api/suggest?q=git%20st")
panel = json.loads(body)["choices"]
assert panel, "the panel has something to explain"
for choice in panel:
    q = urllib.parse.urlencode({"q": "git st", "cmd": choice["cmd"]})
    code, body = get("/api/explain?" + q)
    exp = json.loads(body)
    assert code == 200 and exp["found"], exp
    assert exp["cmd"] == choice["cmd"], exp
    assert abs(exp["score"] - choice["score"]) < 0.01, (exp["score"], choice)
    assert exp["prob"] == choice["prob"], (exp["prob"], choice["prob"])
    assert exp["rank"] is not None and exp["rank"] >= 1, exp
    labels = [f["label"] for f in exp["factors"]]
    assert "frequency" in labels and "recency" in labels, labels
    assert any(f["label"] == "length" and f["contrib"] <= 0
               for f in exp["factors"]), "length only ever subtracts"
    total = sum(f["contrib"] for f in exp["factors"])
    assert abs(total - exp["score"]) < 0.01, (total, exp["score"])
q = urllib.parse.urlencode({"q": "git st", "cmd": "no such command"})
code, body = get("/api/explain?" + q)
assert json.loads(body)["found"] is False, "an unrecorded line explains to nothing"
code, body = get("/api/explain?cmd=")
assert json.loads(body)["found"] is False, "an empty cmd is a question about nothing"
code, body = get("/api/explain?q=" + "x" * 600 + "&cmd=" + "y" * 600)
assert code == 200, "an over-long explain question is cut, not an error"
print("OK — /api/explain names the factors, and its total is the panel's score.")

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
