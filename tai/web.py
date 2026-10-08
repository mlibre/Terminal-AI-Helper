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


def _state() -> dict:
    """What tai has learned, as one JSON-able dict. Read-only throughout."""
    from tai import predictor
    from tai.store import count, db_path, load_rows, schema_note, session
    from tai.index import bash_index_path, index_path
    from tai.paths import enabled

    broken = schema_note()
    rows_n, distinct = count()

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
        "path_check_enabled": enabled(),
        "top": [{"cmd": c, "n": n} for c, n in top],
        "recent": [{"cmd": c, "cwd": w, "exit": x, "ts": t} for c, w, x, t in recent],
    }


def _suggest(qs: dict) -> dict:
    from tai import predictor
    prefix = (qs.get("q", [""])[0] or "")[:500]
    cwd = (qs.get("cwd", [""])[0] or "")[:4096]
    last = [s.strip() for s in (qs.get("last", [""])[0] or "").split(";") if s.strip()]
    with _LOCK:
        return predictor.suggest(prefix=prefix, cwd=cwd, last_commands=last[:3],
                                 limit=6)


def _state_locked() -> dict:
    with _LOCK:
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
  :root{--bg:#0d1117;--fg:#c9d1d9;--dim:#8b949e;--line:#21262d;
        --acc:#3fb950;--warn:#d29922;--bad:#f85149;--card:#161b22}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--fg);
       font:14px/1.5 ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;
       padding:24px;max-width:980px;margin-inline:auto}
  h1{font-size:18px;margin:0 0 4px} h1 b{color:var(--acc)}
  .sub{color:var(--dim);font-size:12px;margin-bottom:20px}
  h2{font-size:13px;text-transform:uppercase;letter-spacing:.08em;
     color:var(--dim);margin:28px 0 10px}
  .card{background:var(--card);border:1px solid var(--line);
        border-radius:8px;padding:14px 16px}
  .grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));
        gap:10px}
  .k{color:var(--dim);font-size:11px;text-transform:uppercase;letter-spacing:.06em}
  .v{font-size:20px;margin-top:2px} .v small{font-size:12px;color:var(--dim)}
  input{width:100%;background:#0a0d12;color:var(--fg);
        border:1px solid var(--line);border-radius:6px;padding:10px 12px;
        font:inherit;outline:none}
  input:focus{border-color:var(--acc)}
  .cwd{margin-top:8px;font-size:12px;color:var(--dim);display:flex;gap:8px}
  .cwd input{font-size:12px;padding:6px 10px}
  .ans{margin-top:12px} .ans .top{font-size:16px;color:var(--acc)}
  .ans .top .ghost{color:var(--dim)} .ans .meta{color:var(--dim);font-size:11px}
  table{border-collapse:collapse;width:100%} td,th{padding:4px 10px 4px 0;
       text-align:left;border-bottom:1px solid var(--line);font-size:12px;
       vertical-align:top}
  th{color:var(--dim);font-weight:400;font-size:11px;text-transform:uppercase}
  td.cmd{color:var(--fg);word-break:break-all} td.dim{color:var(--dim)}
  .bar{height:14px;background:var(--acc);opacity:.25;border-radius:3px}
  .row{display:grid;grid-template-columns:1fr 60px;gap:10px;align-items:center;
       margin:6px 0;font-size:12px}
  .row .c{word-break:break-all}
  .err{color:var(--bad)} .ok{color:var(--acc)} .warn{color:var(--warn)}
  .foot{color:var(--dim);font-size:11px;margin-top:28px}
  code{color:var(--acc)}
</style></head><body>
<h1><b>tai</b> dashboard</h1>
<div class="sub">what the helper learned from your shell — read-only, 127.0.0.1</div>

<h2>try a prefix</h2>
<div class="card">
  <input id="q" placeholder="git st…" autofocus spellcheck="false">
  <div class="cwd">cwd <input id="cwd" placeholder="(empty = here)"></div>
  <div class="ans" id="ans"></div>
</div>

<h2>state</h2>
<div class="grid" id="state"></div>

<h2>most-run commands</h2>
<div class="card" id="top"></div>

<h2>latest recorded</h2>
<div class="card"><table><thead><tr><th>when</th><th>command</th><th>cwd</th>
<th>exit</th></tr></thead><tbody id="recent"></tbody></table></div>

<div class="foot">the server binds 127.0.0.1 only and answers GETs alone;
nothing on this page can write to the store.
Feed it the way you feed the prompt: just keep using the shell,
or <code>tai refresh</code> in a terminal.</div>

<script>
"use strict";
const $ = id => document.getElementById(id);
const esc = () => { const d = document.createElement("div"); return t => {
  d.textContent = t == null ? "" : String(t); return d.innerHTML; }; };
const E = esc();
const ago = s => s == null ? "—" :
  s < 60 ? s + "s" : s < 3600 ? Math.floor(s/60) + "m" :
  s < 86400 ? Math.floor(s/3600) + "h" : Math.floor(s/86400) + "d";

function state() {
  fetch("/api/state").then(r => r.json()).then(s => {
    const L = s.learned || {};
    const cards = [
      ["rows", s.db.rows.toLocaleString() + ` <small>(${s.db.distinct} distinct)</small>`],
      ["learned commands", L.commands != null ? L.commands.toLocaleString() : "—"],
      ["sequence entries", L.sequence_entries != null ? L.sequence_entries.toLocaleString() : "—"],
      ["token bigrams", L.token_bigrams != null ? L.token_bigrams.toLocaleString() : "—"],
      ["hidden as stale", L.stale_hidden != null ? L.stale_hidden : "—"],
      ["zsh index", s.indexes.zsh.exists ? "built <small>" + ago(s.indexes.zsh.age_s) + " ago</small>" : "none"],
      ["bash index", s.indexes.bash.exists ? "built <small>" + ago(s.indexes.bash.age_s) + " ago</small>" : "none"],
      ["store", s.db.broken ? `<span class="err">broken</span>` : `<span class="ok">healthy</span>`],
    ];
    $("state").innerHTML = cards.map(([k,v]) =>
      `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join("");
    if (s.db.broken) $("state").insertAdjacentHTML("beforeend",
      `<div class="card"><div class="k">store</div><div class="v err">${E(s.db.broken)}</div></div>`);
    const mx = s.top.length ? s.top[0].n : 1;
    $("top").innerHTML = s.top.length ? s.top.map(r =>
      `<div class="row"><span class="c">${E(r.cmd)}</span>` +
      `<span><span class="bar" style="display:block;width:${Math.max(4,100*r.n/mx)}%"></span>` +
      `<span class="k">${r.n}</span></span></div>`).join("") :
      `<div class="k">nothing recorded yet — run commands in the shell</div>`;
    $("recent").innerHTML = s.recent.length ? s.recent.map(r => {
      const t = new Date(r.ts * 1000), now = (Date.now() / 1000) | 0;
      return `<tr><td class="dim">${ago(Math.max(0, now - r.ts))}</td>` +
        `<td class="cmd">${E(r.cmd)}</td><td class="dim">${E(r.cwd)}</td>` +
        `<td class="${r.exit ? "warn" : "dim"}">${r.exit}</td></tr>`; }).join("") :
      `<tr><td class="k" colspan="4">no rows</td></tr>`;
  }).catch(e => $("state").innerHTML = `<div class="card err">${E(e)}</div>`);
}

let tm;
function suggest() {
  clearTimeout(tm);
  tm = setTimeout(() => {
    const q = $("q").value, cwd = $("cwd").value;
    const u = "/api/suggest?q=" + encodeURIComponent(q) +
              "&cwd=" + encodeURIComponent(cwd);
    fetch(u).then(r => r.json()).then(res => {
      if (!q) { $("ans").innerHTML = ""; return; }
      const ch = res.choice || "";
      const ghost = ch && ch.startsWith(q) ?
        `<span class="ghost">${E(ch.slice(q.length))}</span>` : "";
      const rows = (res.choices || []).map(c =>
        `<tr><td class="cmd">${E(c.cmd)}</td>` +
        `<td class="dim">${(c.prob * 100).toFixed(1)}%</td>` +
        `<td class="dim">${c.score}</td></tr>`).join("");
      $("ans").innerHTML =
        `<div class="top">${E(q)}${ghost}</div>` +
        `<div class="meta">engine ${(res.latency_ms || 0).toFixed ? (res.latency_ms || 0).toFixed(2) : res.latency_ms}ms · ${res.source || "ranking"}</div>` +
        (rows ? `<table>${rows}</table>` : `<div class="k">no answer</div>`);
    }).catch(e => $("ans").innerHTML = `<div class="err">${E(e)}</div>`);
  }, 120);
}
$("q").addEventListener("input", suggest);
$("cwd").addEventListener("input", suggest);

state(); setInterval(state, 5000);
</script></body></html>
"""
