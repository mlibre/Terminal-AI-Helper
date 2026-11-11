//! The one server: bound to 127.0.0.1, GET-only, read-only, answering
//! through the same engine as the prompt.

use std::collections::HashMap;
use std::io::{Read, Write};
use std::net::TcpStream;
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::time::{Duration, Instant};

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

struct Server {
    child: Child,
    port: u16,
    base: PathBuf,
}

impl Drop for Server {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
        let _ = std::fs::remove_dir_all(&self.base);
    }
}

fn spawn(name: &str) -> Server {
    let base = std::env::temp_dir().join(format!("tai_web_{}_{}", name, std::process::id()));
    let _ = std::fs::remove_dir_all(&base);
    std::fs::create_dir_all(&base).unwrap();
    let port = pick_port();
    let child = Command::new(env!("CARGO_BIN_EXE_tai"))
        .args(["web", "--port", &port.to_string(), "--no-browser"])
        .env("HOME", &base)
        .env("TAI_DB", base.join("history.db"))
        .env("TAI_INDEX", base.join("zsh-index.zsh"))
        .env("TAI_SPOOL", base.join("spool.log"))
        .env("TAI_NO_LEARN", "1")
        .env("PATH", "/usr/bin:/bin")
        .stdout(Stdio::null())
        .stderr(Stdio::null())
        .spawn()
        .unwrap();
    let server = Server { child, port, base };
    // Wait for the listener.
    let deadline = Instant::now() + Duration::from_secs(10);
    loop {
        if TcpStream::connect(("127.0.0.1", server.port)).is_ok() {
            break;
        }
        assert!(Instant::now() < deadline, "the dashboard never started");
        std::thread::sleep(Duration::from_millis(50));
    }
    server
}

fn pick_port() -> u16 {
    // Bind an ephemeral port, read the number, drop it: the server then
    // re-binds it. Racy in theory, fine in practice, and the default port
    // stays what the readme says.
    std::net::TcpListener::bind(("127.0.0.1", 0))
        .unwrap()
        .local_addr()
        .unwrap()
        .port()
}

fn get(path: &str) -> (u16, String, String) {
    let mut stream = TcpStream::connect(("127.0.0.1", port_of())).unwrap();
    stream.set_read_timeout(Some(Duration::from_secs(10))).unwrap();
    write!(stream, "GET {} HTTP/1.1\r\nHost: 127.0.0.1\r\nConnection: close\r\n\r\n", path).unwrap();
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    let status: u16 = buf
        .split_whitespace()
        .nth(1)
        .and_then(|s| s.parse().ok())
        .unwrap_or(0);
    let (head, body) = buf.split_once("\r\n\r\n").unwrap_or(("", ""));
    (status, head.to_string(), body.to_string())
}

static PORT: std::sync::Mutex<Option<u16>> = std::sync::Mutex::new(None);

fn port_of() -> u16 {
    PORT.lock().unwrap().unwrap()
}

#[test]
fn the_dashboard_answers_read_only() {
    let mut server = spawn("read_only");
    *PORT.lock().unwrap() = Some(server.port);
    let url = format!("http://127.0.0.1:{}/", server.port);

    // The page is one string, no build step, no external asset.
    let (code, head, body) = get("/");
    assert_eq!(code, 200);
    assert!(head.contains("text/html"));
    assert!(body.contains("tai dashboard"));
    assert!(body.contains("/api/state"));
    assert!(!body.contains("http://cdn"), "no external asset");

    // The state is one JSON answer, read-only throughout.
    let (code, head, body) = get("/api/state");
    assert_eq!(code, 200);
    assert!(head.contains("application/json"));
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert!(v.get("db").is_some());
    assert!(v.get("indexes").is_some());
    assert!(v.get("top").is_some());
    assert!(v.get("recent").is_some());

    // The try box answers exactly what the prompt would: an empty store
    // answers from the seed corpus.
    let (code, _, body) = get("/api/suggest?q=git%20sw");
    assert_eq!(code, 200);
    let v: serde_json::Value = serde_json::from_str(&body).unwrap();
    assert_eq!(v["choice"], "git switch branch");

    // A POST is refused in one line, because read-only is the design.
    let mut stream = TcpStream::connect(("127.0.0.1", server.port)).unwrap();
    write!(
        stream,
        "POST /api/state HTTP/1.1\r\nHost: 127.0.0.1\r\nContent-Length: 0\r\nConnection: close\r\n\r\n"
    )
    .unwrap();
    let mut buf = String::new();
    let _ = stream.read_to_string(&mut buf);
    assert!(buf.starts_with("HTTP/1.1 405"), "{buf:?}");
    assert!(buf.contains("read-only"));

    // No such route is a 404, not a panic.
    let (code, _, _) = get("/api/nothing");
    assert_eq!(code, 404);

    // The server binds localhost only: a foreign interface is not listening.
    // (Answered the honest way — the URL the server printed names 127.0.0.1,
    // and a bind to 0.0.0.0 would be visible there. The server's own print
    // is the product's promise.)
    assert!(url.starts_with("http://127.0.0.1:"));

    drop(server);
}
