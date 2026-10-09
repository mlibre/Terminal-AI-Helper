#!/usr/bin/env python3
"""tai web — a localhost dashboard of what tai has learned. Stdlib only.

    tai web [--port N] [--no-browser]      (alias: `tai dashboard`)

One page, two read-only endpoints, no dependencies. The point is to show the
things the terminal cannot: the whole learned table with its scores, the
sequence pairs, the state of the indexes — and a box where typing a prefix
answers exactly what `tai suggest` would answer, through the same engine,
so the page can never disagree with the prompt.

The server binds 127.0.0.1 and nothing else. The database is the user's shell
history; a dashboard that listens on every interface turns a convenience into
a disclosure. Every route is a GET, every handler is read-only, and nothing
here can write, delete or refresh anything — a dashboard that mutates its
subject is a second control surface that has to be defended.
"""
import json
import os
import subprocess
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

DEFAULT_PORT = 8247

# One lock around every read. The predictor caches its engine in a module
# global built on first use, and ThreadingHTTPServer means two browser tabs
# can ask at once; a dashboard that races its own engine into two builds is
# slower, not faster, and the answer never needed parallelism to be right.
_LOCK = threading.Lock()

# The store the warmed engine was built from, as the (size, mtime_ns) the
# engine disk cache keys on. A dashboard server is long-lived; without this
# key its engine froze at startup and the panel went on explaining
# yesterday's history while the tables below it moved.
_DB_KEY: tuple | None = None


def _fresh_engine() -> None:
    """Drop the warmed engine when the store has changed underneath it.

    Called under the lock from every route that answers through the engine,
    so the rebuild cannot race another tab's question. The key is the same
    one tai.engcache uses, so a record invalidates the engine the moment its
    write lands, and the next question pays one rebuild — the same price the
    shell pays once per store change, never per keystroke.
    """
    global _DB_KEY
    from tai import predictor
    from tai.store import db_path
    try:
        st = db_path().stat()
        key = (st.st_size, st.st_mtime_ns)
    except OSError:
        return                      # no store yet: nothing to be fresh about
    if key != _DB_KEY:
        predictor._E = None
        predictor._STALE = frozenset()
        _DB_KEY = key


# The git context of the cwd box, derived the way the shell plugin derives
# its own: toplevel and branch of the directory asked about. The shell hands
# the engine `--git … --branch …` with every question; a dashboard asked
# about a directory should ask the same question, or the repository and
# branch factors read zero for a command the prompt would have scored.
_GIT_TTL = 10.0
_GIT_CACHE: dict[str, tuple[float, str, str]] = {}


def _git_of(cwd: str) -> tuple[str, str]:
    """(repo, branch) for `cwd`, cached briefly.

    Cached because suggest runs per keystroke and a subprocess per keystroke
    is a dashboard that lags its own typing; the TTL is what lets a user
    switch branches and see the panel follow within seconds rather than at
    the next page load. An empty answer caches like any other — a directory
    that is not a repository stays one for the TTL.
    """
    if not cwd:
        return "", ""
    now = time.time()
    hit = _GIT_CACHE.get(cwd)
    if hit and now - hit[0] < _GIT_TTL:
        return hit[1], hit[2]
    if len(_GIT_CACHE) > 64:        # a page that wanders many directories
        _GIT_CACHE.clear()
    repo = branch = ""
    if os.path.isdir(cwd):
        try:
            r = subprocess.run(["git", "-C", cwd, "rev-parse", "--show-toplevel"],
                               capture_output=True, text=True, timeout=2)
            if r.returncode == 0:
                repo = r.stdout.strip()
                b = subprocess.run(
                    ["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                    capture_output=True, text=True, timeout=2)
                if b.returncode == 0:
                    branch = b.stdout.strip()
        except Exception:
            pass                    # no git, or a dying subprocess: score without it
    _GIT_CACHE[cwd] = (now, repo, branch)
    return repo, branch


def _question(qs: dict) -> dict:
    """Parse the question the page asked, with the git context of its cwd.

    The one place the panel's question is assembled, shared by suggest and
    explain — the two must ask identically or the explained number and the
    displayed number part ways.
    """
    cwd = os.path.expanduser((qs.get("cwd", [""])[0] or "")[:4096])
    repo, branch = _git_of(cwd)
    return {
        "prefix": (qs.get("q", [""])[0] or "")[:500],
        "cwd": cwd,
        "repo": repo,
        "branch": branch,
        "last": [s.strip() for s in (qs.get("last", [""])[0] or "").split(";")
                 if s.strip()][:3],
    }


def _clean(value, cap: int = 120) -> str:
    """One line of the history, bounded for the page.

    Real histories hold pasted JSON blobs and heredoc fragments —
    multi-kilobyte rows, sometimes with newlines in them from before the
    recorder rejected them. Shipped raw they break the table they render in
    and bloat the state answer until the browser gives up on the fetch (the
    NetworkError a user saw where `state` should have been). Collapsed and
    capped here, once, so every client gets the same small answer and no
    page ever has to truncate on its own.
    """
    s = " ".join(str(value).split())
    return s if len(s) <= cap else s[: cap - 1] + "…"


def _state() -> dict:
    """What tai has learned, as one JSON-able dict. Read-only throughout."""
    from tai import predictor
    from tai.store import count, db_path, load_rows, schema_note, session
    from tai.index import bash_index_path, index_path
    from tai.paths import enabled

    broken = schema_note()
    rows_n, distinct = count()

    # The record path's own self-refresh policy, quoted here rather than
    # restated: how far behind new commands the shell index may lag, and the
    # belt-and-braces rebuild every so many recorded commands. It is the
    # answer to the question every new user asks — "does tai refresh
    # itself?" — read from the constants that actually run.
    try:
        from tai.cli import AUTO_REBUILD_EVERY, _REBUILD_MIN_INTERVAL
        auto = {"debounce_s": _REBUILD_MIN_INTERVAL, "every": AUTO_REBUILD_EVERY}
    except Exception:
        auto = None

    # Warm the predictor's engine once; after this the /api/suggest box and
    # this state share the same object, which is the promise of the page.
    try:
        predictor.suggest("", limit=1)
        eng = predictor._E
        learned = {
            "commands": len(eng.cmds),
            "sequence_entries": sum(len(n) for n in eng.seq.values()),
            "token_bigrams": sum(len(n) for n in eng.token_bigram.values()),
            "stale_hidden": len(predictor._STALE),
        }
    except Exception as e:                      # a broken store is a state to show
        learned = {"error": str(e)}

    top, recent = [], []
    if not broken and rows_n:
        with session() as con:
            top = con.execute(
                "SELECT cmd, COUNT(*) AS n FROM commands GROUP BY cmd "
                "ORDER BY n DESC, cmd LIMIT 25").fetchall()
            recent = con.execute(
                "SELECT cmd, cwd, exit_code, ts FROM commands "
                "ORDER BY id DESC LIMIT 15").fetchall()

    def _age(p) -> int | None:
        try:
            return int(time.time() - os.path.getmtime(p)) if p.exists() else None
        except OSError:
            return None

    z, b = index_path(), bash_index_path()
    return {
        "db": {"path": str(db_path()), "rows": rows_n, "distinct": distinct,
               "broken": broken},
        "indexes": {"zsh": {"path": str(z), "exists": z.exists(),
                            "age_s": _age(z)},
                    "bash": {"path": str(b), "exists": b.exists(),
                             "age_s": _age(b)}},
        "learned": learned,
        "auto_refresh": auto,
        "path_check_enabled": enabled(),
        "top": [{"cmd": _clean(c), "n": n} for c, n in top],
        "recent": [{"cmd": _clean(c), "cwd": _clean(w, 80), "exit": x, "ts": t}
                   for c, w, x, t in recent],
    }


def _suggest(qs: dict) -> dict:
    from tai import predictor
    q = _question(qs)
    with _LOCK:
        _fresh_engine()
        return predictor.suggest(prefix=q["prefix"], cwd=q["cwd"], repo=q["repo"],
                                 branch=q["branch"], last_commands=q["last"],
                                 limit=6)


def _explain(qs: dict) -> dict:
    """Why one suggestion scores what it scores — the panel's arithmetic.

    Answered under the same lock, from the same engine, with the same question
    the suggest box asked: the probability is read back from a fresh suggest so
    the number explained is the number on screen, never a recomputation that
    could have drifted while the user typed.
    """
    from tai import predictor
    q = _question(qs)
    cmd = (qs.get("cmd", [""])[0] or "")[:500]
    if not cmd:
        return {"cmd": cmd, "prefix": q["prefix"], "found": False}
    with _LOCK:
        _fresh_engine()
        res = predictor.suggest(prefix=q["prefix"], cwd=q["cwd"], repo=q["repo"],
                                branch=q["branch"], last_commands=q["last"],
                                limit=6)
        exp = predictor.explain(q["prefix"], cmd, cwd=q["cwd"], repo=q["repo"],
                                branch=q["branch"], last_commands=q["last"])
    if exp is None:
        return {"cmd": cmd, "prefix": q["prefix"], "found": False}
    exp["found"] = True
    # What was actually asked, so the panel can show the directory (and the
    # repository behind it) the answer is about — a breakdown that quotes a
    # directory the reader cannot see is a breakdown they cannot check.
    exp["asked_cwd"] = q["cwd"]
    exp["asked_repo"] = q["repo"]
    exp["asked_branch"] = q["branch"]
    exp["prob"] = None
    exp["rank"] = None
    for i, c in enumerate(res.get("choices", []), 1):
        if c.get("cmd") == cmd:
            exp["prob"] = c.get("prob")
            exp["rank"] = i
            break
    return exp


def _state_locked() -> dict:
    with _LOCK:
        _fresh_engine()             # the learned counts answer through the engine too
        return _state()


class _Handler(BaseHTTPRequestHandler):
    server_version = "tai"

    def _send(self, code: int, body: bytes, ctype: str) -> None:
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        # Same-origin only: no CORS header, so another origin's page cannot
        # read the user's history out of this server from their browser.
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        u = urlparse(self.path)
        try:
            if u.path == "/":
                self._send(200, _PAGE.encode(), "text/html; charset=utf-8")
            elif u.path == "/api/state":
                body = json.dumps(_state_locked()).encode()
                self._send(200, body, "application/json")
            elif u.path == "/api/suggest":
                body = json.dumps(_suggest(parse_qs(u.query))).encode()
                self._send(200, body, "application/json")
            elif u.path == "/api/explain":
                body = json.dumps(_explain(parse_qs(u.query))).encode()
                self._send(200, body, "application/json")
            else:
                self._send(404, b'{"error": "no such route"}', "application/json")
        except BrokenPipeError:
            pass                      # a tab closed mid-answer is not an error
        except Exception as e:
            try:
                self._send(500, json.dumps({"error": str(e)}).encode(),
                           "application/json")
            except Exception:
                pass

    def do_POST(self) -> None:
        # Explicit rather than the default 501: the dashboard is read-only by
        # design, and "method not allowed" says that in one line.
        self._send(405, b'{"error": "read-only"}', "application/json")

    def log_message(self, format: str, *args) -> None:
        pass                          # a page that polls is not a log worth keeping


def serve(port: int = DEFAULT_PORT, open_browser: bool = True) -> int:
    """Bind 127.0.0.1, warm the engine, then serve until Ctrl-C."""
    srv = ThreadingHTTPServer(("127.0.0.1", port), _Handler)
    port = srv.server_address[1]
    url = f"http://127.0.0.1:{port}/"
    t0 = time.time()
    print(f"tai: warming the engine for {url} …", flush=True)
    try:
        _state_locked()
    except Exception as e:
        print(f"tai: the store could not be read ({e}); the dashboard will "
              f"show what it can", file=os.sys.stderr, flush=True)
    print(f"tai dashboard on {url} — localhost only, Ctrl-C to stop "
          f"(engine ready in {time.time() - t0:.1f}s)", flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass                      # headless is normal, the URL is printed
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\ntai: dashboard stopped", flush=True)
    finally:
        srv.server_close()
    return 0


# The page. One string, no build step, no external asset: a dashboard that
# needs npm is not a feature of a tool whose whole selling point is starting
# in one line. Every value lands through textContent — shell history contains
# angle brackets and quotes, and a page that builds markup out of it is an
# XSS in the user's own terminal data.
_PAGE = r"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>tai dashboard</title>
<style>
  :root{--bg:#0d1117;--fg:#e6edf3;--dim:#8b949e;--line:#21262d;
        --acc:#3fb950;--warn:#d29922;--bad:#f85149;--card:#161b22;
        --in:#0a0d12;--hov:#1c2129;--bar:#238636}
  html[data-theme="light"]{--bg:#f6f8fa;--fg:#1f2328;--dim:#59636e;
        --line:#d1d9e0;--acc:#1a7f37;--warn:#9a6700;--bad:#cf222e;
        --card:#ffffff;--in:#ffffff;--hov:#eef1f4;--bar:#2da44e}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;
       padding:28px 20px 60px}
  .wrap{max-width:980px;margin-inline:auto}
  header{display:flex;align-items:flex-start;justify-content:space-between;gap:12px}
  h1{font-size:20px;margin:0;letter-spacing:-.01em}
  h1 b{color:var(--acc)}
  .sub{color:var(--dim);font-size:12.5px;margin-top:3px}
  #theme{border:1px solid var(--line);background:var(--card);color:var(--dim);
         border-radius:8px;padding:7px 14px;font:inherit;font-size:12.5px;
         cursor:pointer;flex:none}
  #theme:hover{color:var(--fg);border-color:var(--dim)}
  #net{display:none;margin:14px 0 0;padding:8px 14px;border-radius:8px;
       border:1px solid var(--warn);color:var(--warn);font-size:12.5px}
  h2{font-size:12px;text-transform:uppercase;letter-spacing:.09em;
     color:var(--dim);margin:30px 0 10px;font-weight:600}
  .card{background:var(--card);border:1px solid var(--line);
        border-radius:10px;padding:16px 18px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:10px}
  .grid .card{padding:12px 14px}
  .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.07em}
  .v{font-size:22px;margin-top:3px;font-variant-numeric:tabular-nums}
  .v small{font-size:12px;color:var(--dim)}
  input{width:100%;background:var(--in);color:var(--fg);
        border:1px solid var(--line);border-radius:8px;padding:10px 13px;
        font:15px/1.4 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
        outline:none;transition:border-color .15s}
  input:focus{border-color:var(--acc)}
  input::placeholder{color:var(--dim)}
  .cwd{margin-top:9px;display:flex;align-items:center;gap:9px;color:var(--dim);font-size:12px}
  .cwd input{font-size:12.5px;padding:6px 10px}
  #cwdhint{flex:none;font-size:11px;white-space:nowrap}
  .ans{margin-top:14px}
  .top{font:16px ui-monospace,SFMono-Regular,Menlo,Consolas,monospace}
  .top .ghost{color:var(--dim)}
  .meta{color:var(--dim);font-size:11.5px;margin:5px 0 10px}
  table{border-collapse:collapse;width:100%}
  th{color:var(--dim);font-weight:500;font-size:11px;text-transform:uppercase;
     letter-spacing:.06em;text-align:left;padding:6px 10px 6px 0}
  .ans td{padding:6px 10px 6px 0;font-size:13px;border-top:1px solid var(--line)}
  .ans tr:first-child td{border-top:none}
  .ans tbody tr{cursor:pointer}
  .ans tbody tr:hover{background:var(--hov)}
  .ans td.cmd{font:13px ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
  .pbar{height:6px;background:var(--bar);border-radius:3px;opacity:.85}
  .num{color:var(--dim);font-size:12px;white-space:nowrap;font-variant-numeric:tabular-nums}
  button.why{border:1px solid var(--line);background:transparent;color:var(--dim);
             border-radius:6px;padding:2px 9px;font:inherit;font-size:11.5px;cursor:pointer}
  button.why:hover{color:var(--fg);border-color:var(--dim)}
  tr.xrow td{background:var(--in);padding:10px 14px;border-top:1px dashed var(--line)}
  .xhead{font-size:12.5px;margin-bottom:7px;color:var(--fg)}
  .xhead b{color:var(--acc)}
  table.xt{width:100%;border-collapse:collapse}
  table.xt td{border:none;padding:3px 14px 3px 0;font-size:12px;vertical-align:top}
  table.xt td:first-child{color:var(--fg);white-space:nowrap}
  table.xt td:last-child{color:var(--dim)}
  .pos{color:var(--acc)} .neg{color:var(--bad)}
  #recent td{padding:7px 10px 7px 0;font-size:12.5px;border-top:1px solid var(--line);vertical-align:top}
  #recent tbody tr:hover{background:var(--hov)}
  td.cmd,td.cwd{font:12.5px ui-monospace,Menlo,Consolas,monospace;word-break:break-all}
  .dim{color:var(--dim)}
  .badge{display:inline-block;min-width:22px;text-align:center;border-radius:5px;
         padding:1px 6px;font-size:11.5px;font-variant-numeric:tabular-nums}
  .ok{color:var(--acc)} .warn{color:var(--warn)} .err{color:var(--bad)}
  .row{display:grid;grid-template-columns:1fr 110px;gap:12px;align-items:center;
       margin:8px 0;font-size:13px}
  .row .c{word-break:break-all;font:13px ui-monospace,Menlo,Consolas,monospace}
  .row:hover .c{color:var(--acc)}
  .row .n{display:block;margin-bottom:3px}
  .row .bar{height:8px;background:var(--bar);border-radius:4px;opacity:.8}
  .foot{color:var(--dim);font-size:11.5px;margin-top:34px;line-height:1.6}
  code{color:var(--acc);font-size:.95em}
  #toast{position:fixed;left:50%;bottom:26px;transform:translateX(-50%) translateY(8px);
         background:var(--fg);color:var(--bg);padding:8px 16px;border-radius:8px;
         font-size:12.5px;opacity:0;pointer-events:none;transition:all .18s}
  #toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
</style></head><body>
<div class="wrap">
<header>
  <div><h1><b>tai</b> dashboard</h1>
  <div class="sub">what the helper learned from your shell — read-only, 127.0.0.1</div></div>
  <button id="theme" aria-label="toggle color theme">light</button>
</header>
<div id="net">lost the server — retrying…</div>

<h2>try a prefix</h2>
<div class="card">
  <input id="q" placeholder="git st…" autofocus spellcheck="false" autocomplete="off">
  <div class="cwd">cwd <input id="cwd" placeholder="auto-fills from your latest record"
       spellcheck="false" autocomplete="off"><span id="cwdhint" class="dim"></span></div>
  <div class="ans" id="ans"></div>
</div>

<h2>state</h2>
<div class="grid" id="state"></div>

<h2>most-run commands</h2>
<div class="card" id="top"></div>

<h2>latest recorded</h2>
<div class="card" style="padding:8px 18px"><table id="recent"></table></div>

<div class="foot">the server binds 127.0.0.1 only and answers GETs alone;
nothing on this page can write to the store. Feed it the way you feed the
prompt: just keep using the shell — the shell index rebuilds itself within
seconds of new commands — or <code>tai refresh</code> in a terminal.
Click a suggestion to copy it; click a cwd below to ask about that directory;
<code>why</code> opens its arithmetic.</div>
</div>
<div id="toast"></div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = () => { const d = document.createElement("div"); return t => {
  d.textContent = t == null ? "" : String(t); return d.innerHTML; }; };
const E = esc();
const ago = s => s == null ? "—" :
  s < 60 ? s + "s" : s < 3600 ? Math.floor(s/60) + "m" :
  s < 86400 ? Math.floor(s/3600) + "h" : Math.floor(s/86400) + "d";

// Theme: dark is the terminal default; an explicit choice persists, and the
// system preference is honored only until the user picks a side.
const root = document.documentElement;
function setTheme(t) {
  root.dataset.theme = t;
  $("theme").textContent = t === "light" ? "dark" : "light";
  try { localStorage.setItem("tai-theme", t); } catch (e) {}
}
$("theme").addEventListener("click", () =>
  setTheme(root.dataset.theme === "light" ? "dark" : "light"));
try {
  const saved = localStorage.getItem("tai-theme");
  if (saved) setTheme(saved);
  else if (window.matchMedia && matchMedia("(prefers-color-scheme: light)").matches) {
    root.dataset.theme = "light";
    $("theme").textContent = "dark";
  }
} catch (e) {}

let toastTm;
function toast(msg) {
  const t = $("toast"); t.textContent = msg; t.classList.add("show");
  clearTimeout(toastTm); toastTm = setTimeout(() => t.classList.remove("show"), 1300);
}

// Why this score: one fetch per open, the breakdown rendered through
// textContent like everything else — the details quote the user's own
// directories and commands, and innerHTML would make that an XSS again.
function why(btn, cmd) {
  const tr = btn.closest("tr");
  const open = tr.nextElementSibling;
  if (open && open.classList.contains("xrow")) { open.remove(); return; }
  document.querySelectorAll("#ans tr.xrow").forEach(x => x.remove());
  const u = "/api/explain?q=" + encodeURIComponent($("q").value) +
            "&cwd=" + encodeURIComponent($("cwd").value) +
            "&cmd=" + encodeURIComponent(cmd);
  btn.textContent = "…";
  fetch(u).then(r => r.json()).then(x => {
    btn.textContent = "why";
    const row = document.createElement("tr"); row.className = "xrow";
    const td = document.createElement("td"); td.colSpan = 5;
    if (!x.found) {
      td.textContent = "nothing recorded for this line — its score is a seed or a guess";
    } else {
      const head = document.createElement("div"); head.className = "xhead";
      const b = document.createElement("b"); b.textContent = "score " + x.score;
      head.appendChild(b);
      if (x.prob != null)
        head.appendChild(document.createTextNode("  →  " + (x.prob * 100).toFixed(1) + "% of the answer"));
      if (x.rank != null)
        head.appendChild(document.createTextNode("  ·  rank " + x.rank));
      if (x.stale)
        head.appendChild(document.createTextNode("  ·  hidden as stale (its path is gone)"));
      td.appendChild(head);
      // The directory the question was asked about — the same one the shell
      // would have asked from, filled from your latest record. A breakdown
      // that quotes a directory you cannot see is one you cannot check.
      if (x.asked_cwd)
        head.appendChild(document.createTextNode("  ·  in " + x.asked_cwd));
      const t = document.createElement("table"); t.className = "xt";
      x.factors.forEach(f => {
        const r2 = document.createElement("tr");
        const a = document.createElement("td"); a.textContent = f.label;
        const c2 = document.createElement("td");
        c2.textContent = (f.contrib >= 0 ? "+" : "") + f.contrib.toFixed(3);
        c2.className = "num " + (f.contrib >= 0 ? "pos" : "neg");
        const d = document.createElement("td"); d.textContent = f.detail;
        r2.append(a, c2, d); t.appendChild(r2);
      });
      td.appendChild(t);
      const foot = document.createElement("div"); foot.className = "k";
      foot.style.marginTop = "7px";
      foot.textContent = x.asked_cwd ?
        "the % is this score beside every other candidate's (softmax over the panel)" :
        "no directory in the question — the cwd box above would ask one";
      td.appendChild(foot);
    }
    row.appendChild(td); tr.after(row);
  }).catch(() => { btn.textContent = "why"; $("net").style.display = "block"; });
}

function copy(cmd) {
  const short = cmd.length > 40 ? cmd.slice(0, 40) + "…" : cmd;
  const done = () => toast("copied  " + short);
  if (navigator.clipboard && navigator.clipboard.writeText)
    navigator.clipboard.writeText(cmd).then(done, () => toast("copy failed"));
  else toast("copy failed");
}

function card(k, v) {
  return `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`;
}

// A failed poll shows the banner and keeps the last good page on screen —
// a dashboard that blanks itself on one dropped request is a dashboard the
// user cannot trust to come back.

// The cwd box is the panel's directory question. A browser has no shell, so
// "here" arrives from the history itself: the latest record's cwd, filled
// once, until you edit the box — your edit always wins.
let cwdTouched = false, cwdFilled = false;
function askCwd(path, why) {
  $("cwd").value = path;
  cwdFilled = true;
  $("cwdhint").textContent = why || "";
  suggest();
}
$("cwd").addEventListener("input", () => {
  cwdTouched = true;
  $("cwdhint").textContent = "";
  suggest();
});

function state() {
  fetch("/api/state").then(r => r.json()).then(s => {
    $("net").style.display = "none";
    if (!cwdTouched && !cwdFilled && s.recent.length && s.recent[0].cwd)
      askCwd(s.recent[0].cwd, "from your latest record — edit to ask elsewhere");
    const L = s.learned || {};
    $("state").innerHTML =
      card("rows", s.db.rows.toLocaleString() + ` <small>(${s.db.distinct} distinct)</small>`) +
      card("learned commands", L.commands != null ? L.commands.toLocaleString() : "—") +
      card("sequence entries", L.sequence_entries != null ? L.sequence_entries.toLocaleString() : "—") +
      card("token bigrams", L.token_bigrams != null ? L.token_bigrams.toLocaleString() : "—") +
      card("hidden as stale", L.stale_hidden != null ? L.stale_hidden : "—") +
      card("zsh index", s.indexes.zsh.exists ? "built <small>" + ago(s.indexes.zsh.age_s) + " ago</small>" : "none") +
      card("bash index", s.indexes.bash.exists ? "built <small>" + ago(s.indexes.bash.age_s) + " ago</small>" : "none") +
      card("auto-refresh", s.auto_refresh ?
        `≤ ${s.auto_refresh.debounce_s}s <small>behind new commands · full rebuild every ${s.auto_refresh.every}th</small>` : "—") +
      card("store", s.db.broken ? `<span class="err">broken</span>` : `<span class="ok">healthy</span>`) +
      (s.db.broken ? card("store error", `<span class="err">${E(s.db.broken)}</span>`) : "");
    const mx = s.top.length ? s.top[0].n : 1;
    $("top").innerHTML = s.top.length ? s.top.map(r =>
      `<div class="row"><span class="c">${E(r.cmd)}</span>` +
      `<span><span class="k n">${r.n}</span><span class="bar" style="display:block;width:${Math.max(3, 100 * r.n / mx)}%"></span></span></div>`).join("") :
      `<div class="k">nothing recorded yet — run commands in the shell</div>`;
    const now = (Date.now() / 1000) | 0;
    // The cwd rides in a side array like the commands do — innerHTML escaping
    // does not touch quotes, and a quoted path in an attribute would break out.
    const cwds = s.recent.map(r => r.cwd);
    $("recent").innerHTML = s.recent.length ?
      "<thead><tr><th>when</th><th>command</th><th>cwd</th><th>exit</th></tr></thead><tbody>" +
      s.recent.map((r, i) =>
        `<tr><td class="dim" title="${new Date(r.ts * 1000).toLocaleString()}">${ago(Math.max(0, now - r.ts))}</td>` +
        `<td class="cmd">${E(r.cmd)}</td>` +
        `<td class="cwd dim" data-i="${i}" title="ask this directory" style="cursor:pointer">${E(r.cwd)}</td>` +
        `<td>${r.exit ? `<span class="badge warn">${r.exit}</span>` : `<span class="badge ok">0</span>`}</td></tr>`).join("") +
      "</tbody>" :
      `<tbody><tr><td class="k">no rows</td></tr></tbody>`;
    $("recent").querySelectorAll("td.cwd[data-i]").forEach(td =>
      td.addEventListener("click", () => askCwd(cwds[+td.dataset.i], "from your latest records — edit to ask elsewhere")));
  }).catch(() => { $("net").style.display = "block"; });
}

let tm;
function suggest() {
  clearTimeout(tm);
  tm = setTimeout(() => {
    const q = $("q").value, cwd = $("cwd").value;
    if (!q) { $("ans").innerHTML = ""; return; }
    const u = "/api/suggest?q=" + encodeURIComponent(q) +
              "&cwd=" + encodeURIComponent(cwd);
    fetch(u).then(r => r.json()).then(res => {
      // Commands ride in a side array, not a data- attribute: innerHTML
      // escaping does not touch quotes, and a quoted command in an attribute
      // would break out of it. The row carries an index, nothing else.
      const ch = res.choices || [];
      const cmds = ch.map(c => c.cmd);
      const best = res.choice || "";
      const ghost = best && best.startsWith(q) ?
        `<span class="ghost">${E(best.slice(q.length))}</span>` : "";
      const top = ch.length ? ch[0].prob || 1 : 1;
      const rows = ch.map((c, i) =>
        `<tr data-i="${i}" title="click to copy">` +
        `<td class="cmd">${E(c.cmd)}</td>` +
        `<td style="width:90px"><span class="pbar" style="display:block;width:${Math.max(2, 100 * (c.prob || 0) / (top || 1))}%"></span></td>` +
        `<td class="num">${((c.prob || 0) * 100).toFixed(1)}%</td>` +
        `<td class="num">${c.score}</td>` +
        `<td style="width:1px"><button class="why" data-w="${i}" aria-label="why this score">why</button></td></tr>`).join("");
      $("ans").innerHTML =
        `<div class="top">${E(q)}${ghost}</div>` +
        `<div class="meta">engine ${(res.latency_ms || 0).toFixed ? (res.latency_ms || 0).toFixed(2) : res.latency_ms}ms · ${res.source || "ranking"}</div>` +
        (rows ? `<table>${rows}</table>` : `<div class="k">no answer</div>`);
      $("ans").querySelectorAll("tr[data-i]").forEach(tr =>
        tr.addEventListener("click", () => copy(cmds[+tr.dataset.i])));
      $("ans").querySelectorAll("button.why").forEach(btn =>
        btn.addEventListener("click", ev => { ev.stopPropagation(); why(btn, cmds[+btn.dataset.w]); }));
    }).catch(() => { $("net").style.display = "block"; });
  }, 120);
}
$("q").addEventListener("input", suggest);

state(); setInterval(state, 5000);
</script></body></html>
"""
