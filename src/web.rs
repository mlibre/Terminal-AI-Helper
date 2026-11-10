//! tai web — a localhost dashboard of what tai has learned.
//!
//! Port of `tai/web.py`. One page, two read-only endpoints, no dependencies.
//! The server binds 127.0.0.1 and nothing else. Every route is a GET, every
//! handler is read-only, and nothing here can write, delete or refresh
//! anything — a dashboard that mutates its subject is a second control
//! surface that has to be defended.

use std::io::{BufRead, BufReader, Read, Write};
use std::net::{TcpListener, TcpStream};
use std::sync::Mutex;
use std::time::{Duration, Instant};

use crate::paths;
use crate::predictor;
use crate::store;

const DEFAULT_PORT: u16 = 8247;

// The git context of the cwd box, derived the way the shell plugin derives
// its own: toplevel and branch of the directory asked about. Cached because
// suggest runs per keystroke and a subprocess per keystroke is a dashboard
// that lags its own typing; an empty answer caches like any other.
const GIT_TTL: f64 = 10.0;

fn git_cache() -> &'static Mutex<std::collections::HashMap<String, (Instant, String, String)>> {
    static CACHE: std::sync::OnceLock<Mutex<std::collections::HashMap<String, (Instant, String, String)>>> =
        std::sync::OnceLock::new();
    CACHE.get_or_init(|| Mutex::new(std::collections::HashMap::new()))
}

fn git_of(cwd: &str) -> (String, String) {
    if cwd.is_empty() {
        return (String::new(), String::new());
    }
    let cache = git_cache();
    if let Ok(map) = cache.lock() {
        if let Some((at, repo, branch)) = map.get(cwd) {
            if at.elapsed().as_secs_f64() < GIT_TTL {
                return (repo.clone(), branch.clone());
            }
        }
    }
    let mut repo = String::new();
    let mut branch = String::new();
    if std::path::Path::new(cwd).is_dir() {
        let (out, _) = crate::util::run_with_timeout(
            &["git", "-C", cwd, "rev-parse", "--show-toplevel"],
            2.0,
            None,
            &[],
        );
        if !out.trim().is_empty() {
            repo = out.trim().to_string();
            let (bout, _) = crate::util::run_with_timeout(
                &["git", "-C", cwd, "rev-parse", "--abbrev-ref", "HEAD"],
                2.0,
                None,
                &[],
            );
            branch = bout.trim().to_string();
        }
    }
    if let Ok(mut map) = cache.lock() {
        if map.len() > 64 {
            map.clear(); // a page that wanders many directories
        }
        map.insert(cwd.to_string(), (Instant::now(), repo.clone(), branch.clone()));
    }
    (repo, branch)
}

/// One line of the history, bounded for the page.
fn clean(value: &str, cap: usize) -> String {
    let s = value.split_whitespace().collect::<Vec<_>>().join(" ");
    if s.chars().count() <= cap {
        s
    } else {
        let cut: String = s.chars().take(cap - 1).collect();
        format!("{}…", cut)
    }
}

/// What tai has learned, as one JSON-able dict. Read-only throughout.
fn state_json() -> serde_json::Value {
    let broken = store::schema_note();
    let (rows_n, distinct) = store::count();

    // The record path's own self-refresh policy, quoted here rather than
    // restated.
    let auto = serde_json::json!({
        "debounce_s": crate::cli::REBUILD_MIN_INTERVAL,
        "every": crate::cli::auto_rebuild_every(),
    });

    // Warm the predictor's engine once; after this the /api/suggest box and
    // this state share the same object, which is the promise of the page.
    let learned = match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        predictor::suggest("", "", "", "", &[], 1, None);
    })) {
        Ok(_) => {
            let guard = predictor::shared_engine();
            match guard.as_ref() {
                Some(w) => {
                    let mut stale = predictor::LazyStale::new(&w.eng, w.stale.cwd());
                    let _ = &mut stale;
                    serde_json::json!({
                        "commands": w.eng.cmds.len(),
                        "sequence_entries": w.eng.seq.values().map(|m| m.len()).sum::<usize>(),
                        "token_bigrams": w.eng.token_bigram.values().map(|m| m.len()).sum::<usize>(),
                    })
                }
                None => serde_json::json!({}),
            }
        }
        Err(_) => serde_json::json!({}),
    };

    let mut top: Vec<(String, i64)> = Vec::new();
    let mut recent: Vec<(String, String, i64, i64)> = Vec::new();
    if broken.is_empty() && rows_n > 0 {
        if let Ok(con) = store::connect() {
            if let Ok(mut stmt) =
                con.prepare("SELECT cmd, COUNT(*) AS n FROM commands GROUP BY cmd ORDER BY n DESC, cmd LIMIT 25")
            {
                if let Ok(rows) =
                    stmt.query_map([], |r| Ok((r.get::<_, String>(0)?, r.get::<_, i64>(1)?)))
                {
                    top = rows.filter_map(|x| x.ok()).collect();
                }
            }
            if let Ok(mut stmt) = con.prepare(
                "SELECT cmd, cwd, exit_code, ts FROM commands ORDER BY id DESC LIMIT 15",
            ) {
                if let Ok(rows) = stmt.query_map([], |r| {
                    Ok((
                        r.get::<_, String>(0)?,
                        r.get::<_, String>(1)?,
                        r.get::<_, i64>(2)?,
                        r.get::<_, i64>(3)?,
                    ))
                }) {
                    recent = rows.filter_map(|x| x.ok()).collect();
                }
            }
        }
    }

    fn age(p: &str) -> Option<i64> {
        let md = std::fs::metadata(p).ok()?;
        let mtime = md.modified().ok()?;
        Some(mtime.elapsed().ok()?.as_secs() as i64)
    }

    let z = paths::index_location();
    let b = paths::bash_index_location();
    let top_json: Vec<serde_json::Value> = top
        .iter()
        .map(|(c, n)| serde_json::json!({"cmd": clean(c, 120), "n": n}))
        .collect();
    let recent_json: Vec<serde_json::Value> = recent
        .iter()
        .map(|(c, w, x, t)| {
            serde_json::json!({"cmd": clean(c, 120), "cwd": clean(w, 80), "exit": x, "ts": t})
        })
        .collect();
    serde_json::json!({
        "db": {"path": paths::db_location(), "rows": rows_n, "distinct": distinct, "broken": broken},
        "indexes": {
            "zsh": {"path": z, "exists": std::path::Path::new(&z).exists(),
                    "age_s": age(&z)},
            "bash": {"path": b, "exists": std::path::Path::new(&b).exists(),
                     "age_s": age(&b)},
        },
        "learned": learned,
        "auto_refresh": auto,
        "path_check_enabled": paths::enabled(),
        "top": top_json,
        "recent": recent_json,
    })
}

/// Parse the question the page asked, with the git context of its cwd. The
/// one place the panel's question is assembled, shared by suggest and
/// explain — the two must ask identically or the explained number and the
/// displayed number part ways.
fn question(qs: &std::collections::HashMap<String, String>) -> (String, String, String, String, Vec<String>) {
    let cwd_raw = qs.get("cwd").cloned().unwrap_or_default();
    let cwd: String = paths::expanduser(&cwd_raw).chars().take(4096).collect();
    let (repo, branch) = git_of(&cwd);
    let prefix: String = qs.get("q").cloned().unwrap_or_default().chars().take(500).collect();
    let last: Vec<String> = qs
        .get("last")
        .cloned()
        .unwrap_or_default()
        .split(';')
        .map(|s| s.trim().to_string())
        .filter(|s| !s.is_empty())
        .take(3)
        .collect();
    (prefix, cwd, repo, branch, last)
}

fn suggest_json(qs: &std::collections::HashMap<String, String>) -> serde_json::Value {
    let (prefix, cwd, repo, branch, last) = question(qs);
    predictor::suggest(&prefix, &cwd, &repo, &branch, &last, 6, None)
}

/// Why one suggestion scores what it scores — the panel's arithmetic.
fn explain_json(qs: &std::collections::HashMap<String, String>) -> serde_json::Value {
    let (prefix, cwd, repo, branch, last) = question(qs);
    let cmd: String = qs.get("cmd").cloned().unwrap_or_default().chars().take(500).collect();
    if cmd.is_empty() {
        return serde_json::json!({"cmd": cmd, "prefix": prefix, "found": false});
    }
    let res = predictor::suggest(&prefix, &cwd, &repo, &branch, &last, 6, None);
    let exp = predictor::explain(&prefix, &cmd, &cwd, &repo, &branch, &last);
    let mut exp = match exp {
        Some(e) => e,
        None => return serde_json::json!({"cmd": cmd, "prefix": prefix, "found": false}),
    };
    exp["found"] = serde_json::json!(true);
    // What was actually asked, so the panel can show the directory (and the
    // repository behind it) the answer is about.
    exp["asked_cwd"] = serde_json::json!(cwd);
    exp["asked_repo"] = serde_json::json!(repo);
    exp["asked_branch"] = serde_json::json!(branch);
    exp["prob"] = serde_json::Value::Null;
    exp["rank"] = serde_json::Value::Null;
    if let Some(choices) = res.get("choices").and_then(|c| c.as_array()) {
        for (i, c) in choices.iter().enumerate() {
            if c.get("cmd").and_then(|c| c.as_str()) == Some(cmd.as_str()) {
                exp["prob"] = c.get("prob").cloned().unwrap_or(serde_json::Value::Null);
                exp["rank"] = serde_json::json!(i + 1);
                break;
            }
        }
    }
    exp
}

fn parse_query(q: &str) -> std::collections::HashMap<String, String> {
    let mut out = std::collections::HashMap::new();
    for pair in q.split('&') {
        if pair.is_empty() {
            continue;
        }
        let (k, v) = match pair.split_once('=') {
            Some((k, v)) => (k, v),
            None => (pair, ""),
        };
        out.insert(url_decode(k), url_decode(v));
    }
    out
}

fn url_decode(s: &str) -> String {
    let bytes = s.as_bytes();
    let mut out = Vec::with_capacity(bytes.len());
    let mut i = 0;
    while i < bytes.len() {
        match bytes[i] {
            b'%' if i + 2 < bytes.len() + 1 && i + 2 <= bytes.len() - 1 => {
                let hex = &s[i + 1..i + 3];
                if let Ok(b) = u8::from_str_radix(hex, 16) {
                    out.push(b);
                    i += 3;
                } else {
                    out.push(b'%');
                    i += 1;
                }
            }
            b'+' => {
                out.push(b' ');
                i += 1;
            }
            b => {
                out.push(b);
                i += 1;
            }
        }
    }
    String::from_utf8_lossy(&out).to_string()
}

const PAGE: &str = include_str!("dashboard.html");

fn respond(stream: &mut TcpStream, code: u16, body: &[u8], ctype: &str) {
    let status = match code {
        200 => "OK",
        404 => "Not Found",
        405 => "Method Not Allowed",
        500 => "Internal Server Error",
        _ => "OK",
    };
    let head = format!(
        "HTTP/1.1 {} {}\r\nContent-Type: {}\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
        code,
        status,
        ctype,
        body.len()
    );
    let _ = stream.write_all(head.as_bytes());
    let _ = stream.write_all(body);
    let _ = stream.flush();
}

fn handle(mut stream: TcpStream) {
    let mut reader = BufReader::new(match stream.try_clone() {
        Ok(s) => s,
        Err(_) => return,
    });
    let mut request_line = String::new();
    if reader.read_line(&mut request_line).is_err() {
        return;
    }
    // Drain the headers; nothing in them is needed, but the body of a POST
    // would be misread as a request without the separator being consumed.
    let mut content_length = 0usize;
    for line in reader.lines().map_while(|l| l.ok()) {
        if line.is_empty() {
            break;
        }
        let lower = line.to_lowercase();
        if let Some(v) = lower.strip_prefix("content-length:") {
            content_length = v.trim().parse().unwrap_or(0);
        }
    }
    let parts: Vec<&str> = request_line.split_whitespace().collect();
    let method = parts.first().copied().unwrap_or("");
    let target = parts.get(1).copied().unwrap_or("/");
    if method != "GET" {
        // Explicit rather than a default 501: the dashboard is read-only by
        // design, and "method not allowed" says that in one line.
        if content_length > 0 {
            let mut buf = vec![0u8; content_length.min(65536)];
            let _ = stream.read(&mut buf);
        }
        respond(&mut stream, 405, b"{\"error\": \"read-only\"}", "application/json");
        return;
    }
    let (path, query) = match target.split_once('?') {
        Some((p, q)) => (p, parse_query(q)),
        None => (target, std::collections::HashMap::new()),
    };
    let result = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| match path {
        "/" => Some((200, PAGE.as_bytes().to_vec(), "text/html; charset=utf-8")),
        "/api/state" => Some((
            200,
            serde_json::to_vec(&state_json()).unwrap_or_default(),
            "application/json",
        )),
        "/api/suggest" => Some((
            200,
            serde_json::to_vec(&suggest_json(&query)).unwrap_or_default(),
            "application/json",
        )),
        "/api/explain" => Some((
            200,
            serde_json::to_vec(&explain_json(&query)).unwrap_or_default(),
            "application/json",
        )),
        _ => Some((
            404,
            b"{\"error\": \"no such route\"}".to_vec(),
            "application/json",
        )),
    }));
    match result {
        Ok(Some((code, body, ctype))) => respond(&mut stream, code, &body, ctype),
        Ok(None) => {}
        Err(_) => {
            respond(&mut stream, 500, b"{\"error\": \"internal\"}", "application/json")
        }
    }
    let _ = std::io::Write::flush(&mut stream);
}

/// Bind 127.0.0.1, warm the engine, then serve until Ctrl-C.
pub fn serve(port: u16, open_browser: bool) -> i32 {
    let listener = match TcpListener::bind(("127.0.0.1", port)) {
        Ok(l) => l,
        Err(e) => {
            eprintln!("tai: could not bind 127.0.0.1:{} ({})", port, e);
            return 1;
        }
    };
    let port = listener.local_addr().map(|a| a.port()).unwrap_or(port);
    let url = format!("http://127.0.0.1:{}/", port);
    let t0 = Instant::now();
    crate::cli::cmd_print(&format!("tai: warming the engine for {} …", url));
    if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| {
        state_json();
    }))
    .is_err()
    {
        eprintln!("tai: the store could not be read; the dashboard will show what it can");
    }
    crate::cli::cmd_print(&format!(
        "tai dashboard on {} — localhost only, Ctrl-C to stop (engine ready in {:.1}s)",
        url,
        t0.elapsed().as_secs_f64()
    ));
    if open_browser {
        // Best effort: headless is normal, the URL is printed.
        for opener in ["xdg-open", "open"] {
            if crate::util::which(opener).is_some() {
                let _ = std::process::Command::new(opener)
                    .arg(&url)
                    .stdout(std::process::Stdio::null())
                    .stderr(std::process::Stdio::null())
                    .spawn();
                break;
            }
        }
    }
    for conn in listener.incoming() {
        match conn {
            Ok(stream) => {
                stream.set_nodelay(true).ok();
                std::thread::spawn(move || {
                    stream.set_read_timeout(Some(Duration::from_secs(10))).ok();
                    handle(stream);
                });
            }
            Err(_) => continue,
        }
    }
    0
}
