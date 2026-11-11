//! Small shared helpers: time, PATH lookups, subprocesses with a timeout.

use std::io::Read;
use std::process::{Command, Stdio};
use std::sync::mpsc;
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

pub fn now_epoch() -> i64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs() as i64)
        .unwrap_or(0)
}

pub fn now_millis() -> f64 {
    SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_secs_f64() * 1000.0)
        .unwrap_or(0.0)
}

/// `shutil.which`'s POSIX branch, line for line: a word with a directory part
/// is looked up directly; a hit must exist, be executable, and not be a
/// directory.
pub fn path_lookup(word: &str) -> bool {
    use std::os::unix::fs::PermissionsExt;
    let (dirname, name) = match word.rsplit_once('/') {
        Some((d, n)) => (Some(d), n),
        None => (None, word),
    };
    let dirs: Vec<String> = match dirname {
        Some(d) => vec![d.to_string()],
        None => {
            let path = std::env::var("PATH").unwrap_or_default();
            if path.is_empty() {
                return false;
            }
            path.split(':').map(|s| {
                if s.is_empty() { ".".to_string() } else { s.to_string() }
            }).collect()
        }
    };
    let mut seen = std::collections::HashSet::new();
    for d in dirs {
        let norm = d.clone();
        if !seen.insert(norm) {
            continue;
        }
        let cand = format!("{}/{}", d, name);
        if let Ok(md) = std::fs::metadata(&cand) {
            if md.is_dir() {
                continue;
            }
            if std::fs::metadata(&cand).map(|m| m.permissions().mode() & 0o111 != 0).unwrap_or(false)
            {
                return true;
            }
        }
    }
    false
}

/// The `command -v` answer the CLI asks once per candidate: is this word an
/// executable file somewhere on PATH (or an explicit path)?
pub fn which(name: &str) -> Option<String> {
    if name.contains('/') {
        let p = std::path::Path::new(name);
        if p.is_file() && is_executable(p) {
            return Some(name.to_string());
        }
        return None;
    }
    let path = std::env::var("PATH").unwrap_or_default();
    for d in path.split(':') {
        let d = if d.is_empty() { "." } else { d };
        let cand = format!("{}/{}", d, name);
        let p = std::path::Path::new(&cand);
        if p.is_file() && is_executable(p) {
            return Some(cand);
        }
    }
    None
}

fn is_executable(p: &std::path::Path) -> bool {
    use std::os::unix::fs::PermissionsExt;
    std::fs::metadata(p)
        .map(|m| m.permissions().mode() & 0o111 != 0)
        .unwrap_or(false)
}

/// Public form of the executable test, for directory walks.
pub fn is_executable_path(p: &std::path::Path) -> bool {
    p.is_file() && is_executable(p)
}

/// Python's `round()`: half to even. The milli-scores the plugins read are
/// `round(score * 1000)`, and the two roundings must agree on the .5 that a
/// tie is made of.
pub fn py_round(x: f64) -> f64 {
    let f = x.floor();
    let diff = x - f;
    if diff > 0.5 {
        f + 1.0
    } else if diff < 0.5 {
        f
    } else if (f as i64) % 2 == 0 {
        f
    } else {
        f + 1.0
    }
}

/// Run a command capturing stdout and stderr *separately* with a hard
/// timeout — the `capture_output=True` shape, which `git_context` needs: a
/// fatal on stderr must not be read as a toplevel on stdout.
pub fn run_capture(
    args: &[&str],
    timeout: f64,
    cwd: Option<&str>,
) -> (String, String, bool) {
    let mut cmd = Command::new(args[0]);
    cmd.args(&args[1..]);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.stdin(Stdio::null());
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return (String::new(), e.to_string(), false),
    };
    let mut child = child;
    let mut out_pipe = child.stdout.take().unwrap();
    let mut err_pipe = child.stderr.take().unwrap();
    let (tx_out, rx_out) = mpsc::channel::<Vec<u8>>();
    let (tx_err, rx_err) = mpsc::channel::<Vec<u8>>();
    std::thread::scope(|s| {
        s.spawn(move || {
            let mut buf = Vec::new();
            let _ = out_pipe.read_to_end(&mut buf);
            let _ = tx_out.send(buf);
        });
        s.spawn(move || {
            let mut buf = Vec::new();
            let _ = err_pipe.read_to_end(&mut buf);
            let _ = tx_err.send(buf);
        });
        let deadline = Instant::now() + Duration::from_secs_f64(timeout.max(0.05));
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    let out = rx_out.recv_timeout(Duration::from_secs(2)).unwrap_or_default();
                    let err = rx_err.recv_timeout(Duration::from_secs(2)).unwrap_or_default();
                    let ok = status.success();
                    return (
                        String::from_utf8_lossy(&out).to_string(),
                        String::from_utf8_lossy(&err).to_string(),
                        ok,
                    );
                }
                Ok(None) => {
                    if Instant::now() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        return (String::new(), String::from("timeout"), false);
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(e) => {
                    return (String::new(), e.to_string(), false);
                }
            }
        }
    })
}

/// Run a command with merged output and a hard timeout, like
/// `subprocess.run(args, stdout=PIPE, stderr=STDOUT, stdin=DEVNULL,
/// timeout=t)`. Returns (stdout, "") or ("", reason).
pub fn run_with_timeout(
    args: &[&str],
    timeout: f64,
    cwd: Option<&str>,
    extra_env: &[(&str, &str)],
) -> (String, String) {
    let mut cmd = Command::new(args[0]);
    cmd.args(&args[1..]);
    cmd.stdout(Stdio::piped());
    cmd.stderr(Stdio::piped());
    cmd.stdin(Stdio::null());
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    for (k, v) in extra_env {
        cmd.env(k, v);
    }
    let child = match cmd.spawn() {
        Ok(c) => c,
        Err(e) => return (String::new(), e.to_string()),
    };
    let mut child = child;
    let mut stdout_pipe = child.stdout.take().unwrap();
    let stderr_pipe = child.stderr.take().unwrap();
    let (tx_out, rx_out) = mpsc::channel::<Vec<u8>>();
    let (tx_err, rx_err) = mpsc::channel::<Vec<u8>>();
    std::thread::scope(|s| {
        s.spawn(move || {
            let mut buf = Vec::new();
            let _ = stdout_pipe.read_to_end(&mut buf);
            let _ = tx_out.send(buf);
        });
        let mut err_pipe = stderr_pipe;
        s.spawn(move || {
            let mut buf = Vec::new();
            let _ = err_pipe.read_to_end(&mut buf);
            let _ = tx_err.send(buf);
        });
        let deadline = Instant::now() + Duration::from_secs_f64(timeout.max(0.05));
        loop {
            match child.try_wait() {
                Ok(Some(status)) => {
                    let out = rx_out.recv_timeout(Duration::from_secs(2)).unwrap_or_default();
                    let err = rx_err.recv_timeout(Duration::from_secs(2)).unwrap_or_default();
                    let mut both = out;
                    both.extend_from_slice(&err);
                    let _ = status;
                    let text = String::from_utf8_lossy(&both).to_string();
                    return (text, String::new());
                }
                Ok(None) => {
                    if Instant::now() >= deadline {
                        let _ = child.kill();
                        let _ = child.wait();
                        return (String::new(), "timeout".to_string());
                    }
                    std::thread::sleep(Duration::from_millis(10));
                }
                Err(e) => {
                    return (String::new(), e.to_string());
                }
            }
        }
    })
}

/// Run a command that is expected to succeed quietly; returns its exit code.
pub fn run_quiet(args: &[&str], cwd: Option<&str>) -> i32 {
    let mut cmd = Command::new(args[0]);
    cmd.args(&args[1..]);
    cmd.stdout(Stdio::null());
    cmd.stderr(Stdio::null());
    cmd.stdin(Stdio::null());
    if let Some(d) = cwd {
        cmd.current_dir(d);
    }
    cmd.status().map(|s| s.code().unwrap_or(-1)).unwrap_or(-1)
}

/// One lock for the tests that point TAI_DB/TAI_SPOOL at their own scratch
/// worlds: two suites moving the same env var under each other's feet is the
/// kind of failure that shows up once a week and never in isolation.
#[cfg(test)]
pub static TEST_ENV_LOCK: std::sync::Mutex<()> = std::sync::Mutex::new(());
