//! The npm door: the package npm ships, and what its postinstall does.
//!
//!     cargo test --release --test pkg_npm
//!
//! The registry package is architecture-neutral: plugins, installer, version —
//! and the postinstall fetches or builds the binary. Both facts are checked
//! here against a real `npm pack`, and the hook is run three ways: once with
//! the scripts skip (containers want files on disk only), once with the binary
//! pre-staged — the way a platform package or a previous run would have left
//! it — and once with the fetch aimed at a local server carrying a tarball in
//! the exact shape the GitHub release ships, so the download path runs
//! hermetically too.
//!
//! npm 11.16+ and 12 run install scripts only when allowed by name, so the
//! package also carries a `bin` entry — `tai` → bin/tai.js — that npm links
//! whether or not any script ran; the launcher finds or fetches the binary
//! itself. That contract is checked here as well.
//!
//! node and npm are the floor this suite stands on; without them it says so
//! and skips, the way the pty suite treats a missing zsh.
//!
//! Ported from tests/test_npm.py. The tarball is inspected with the system
//! `tar` (Python used the tarfile module). The tests share one packed
//! tarball (prepared once), but each uses its own sandbox HOME so they can
//! run in parallel; the Python original shared a single HOME and relied on
//! running the guards before the full install.

use std::collections::HashSet;
use std::fs;
use std::io::Write;
use std::os::unix::fs::PermissionsExt;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::sync::OnceLock;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn version() -> String {
    fs::read_to_string(repo().join("VERSION")).unwrap().trim().to_string()
}

fn built_binary() -> PathBuf {
    let release = repo().join("target/release/tai");
    if release.exists() {
        release
    } else {
        repo().join("target/debug/tai")
    }
}

fn which(cmd: &str) -> bool {
    match std::env::var_os("PATH") {
        Some(path) => std::env::split_paths(&path)
            .map(|dir| dir.join(cmd))
            .any(|p| p.is_file()),
        None => false,
    }
}

/// Pack the registry package once and unpack it under pkg/.
fn prepare() -> &'static PathBuf {
    static PREP: OnceLock<PathBuf> = OnceLock::new();
    PREP.get_or_init(|| {
        assert!(which("npm") && which("node"), "npm and node are not installed — the package checks are skipped");
        let version = version();
        let pack_dir = PathBuf::from(format!("/tmp/tai/tai_npm_{}", std::process::id()));
        let _ = fs::remove_dir_all(&pack_dir);
        fs::create_dir_all(&pack_dir).unwrap();

        // -- what the package ships ------------------------------------------
        let packed = Command::new("npm")
            .args(["pack", "--pack-destination"])
            .arg(&pack_dir)
            .current_dir(repo())
            .output()
            .expect("failed to run npm pack");
        assert_eq!(packed.status.code(), Some(0), "npm pack exits 0");
        let tarball = pack_dir.join(format!("terminal-ai-helper-{version}.tgz"));
        assert!(tarball.exists(), "the tarball is named for the version");

        fs::create_dir_all(pack_dir.join("pkg")).unwrap();
        let st = Command::new("tar")
            .arg("-xzf")
            .arg(&tarball)
            .arg("-C")
            .arg(pack_dir.join("pkg"))
            .status()
            .expect("failed to run tar");
        assert!(st.success(), "the npm tarball extracts");
        pack_dir
    })
}

fn tar_names(pack_dir: &Path) -> HashSet<String> {
    let tarball = pack_dir.join(format!("terminal-ai-helper-{}.tgz", version()));
    let out = Command::new("tar")
        .arg("-tzf")
        .arg(&tarball)
        .output()
        .expect("failed to list the npm tarball");
    String::from_utf8_lossy(&out.stdout)
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|n| {
            n.strip_prefix("package/")
                .unwrap_or(n)
                .trim_end_matches('/')
                .to_string()
        })
        .collect()
}

#[test]
fn package_layout() {
    let pack_dir = prepare();
    let files = tar_names(pack_dir);
    assert!(
        ["plugins/tai.zsh", "plugins/tai.bash"].iter().all(|f| files.contains(*f)),
        "the plugins ship"
    );
    assert!(
        files.iter().any(|n| n.starts_with("plugins/zsh/"))
            && files.iter().any(|n| n.starts_with("plugins/bash/")),
        "the plugin parts ship"
    );
    assert!(files.contains("install.sh"), "the installer ships");
    assert!(files.contains("bin/npm-postinstall"), "the postinstall hook ships");
    assert!(files.contains("bin/tai.js"), "the launcher ships");
    assert!(files.contains("VERSION"), "the version file ships");
    assert!(!files.iter().any(|n| n.ends_with(".py")), "no Python ships");
    assert!(!files.iter().any(|n| n.contains("__pycache__")), "no __pycache__ ships");

    let pkg_json: serde_json::Value = serde_json::from_str(
        &fs::read_to_string(pack_dir.join("pkg/package/package.json")).unwrap(),
    )
    .unwrap();
    assert_eq!(
        pkg_json["version"],
        serde_json::json!(version()),
        "package version matches VERSION"
    );
    // npm 11.16+ blocks install scripts unless allowed by name, so the `tai`
    // executable can never hang off the postinstall alone: the bin entry is
    // what npm links on every install, scripts or no scripts. It must point
    // at a file the package actually carries.
    assert_eq!(
        pkg_json["bin"]["tai"],
        serde_json::json!("bin/tai.js"),
        "npm links a tai executable"
    );
}

#[test]
fn postinstall_guards() {
    let pack_dir = prepare();
    let pkg = pack_dir.join("pkg/package");
    let home = pack_dir.join("home");
    let env = |cmd: &mut Command| {
        cmd.env("HOME", &home)
            .env("XDG_DATA_HOME", pack_dir.join("data"));
    };

    let mut cmd = Command::new("bash");
    cmd.arg(pkg.join("bin/npm-postinstall"));
    env(&mut cmd);
    let got = cmd.output().expect("failed to run npm-postinstall");
    assert_eq!(got.status.code(), Some(0), "a non-global install is skipped");
    assert_eq!(String::from_utf8_lossy(&got.stdout), "", "it said nothing");

    let mut cmd = Command::new("bash");
    cmd.arg(pkg.join("bin/npm-postinstall"))
        .env("npm_config_global", "1")
        .env("TAI_SKIP_POSTINSTALL", "1");
    env(&mut cmd);
    let got = cmd.output().expect("failed to run npm-postinstall (skip)");
    assert_eq!(got.status.code(), Some(0), "TAI_SKIP_POSTINSTALL skips it");
    assert!(!home.join(".zshrc").exists(), "it wrote no rc file");
}

#[test]
fn postinstall_full_install() {
    // Its own copy: this run stages a binary into bin/, and a copy is the
    // only way the parallel runs cannot hand one another their leftovers.
    let pack_dir = prepare();
    let pkg = own_pkg_copy(pack_dir, "full");
    // The Python original reused the guards' HOME; this test keeps its own so
    // the two can run in parallel without one's rc file confusing the other.
    let home = pack_dir.join("home_full");
    let bin_dir = home.join(".local/bin");
    fs::create_dir_all(&bin_dir).unwrap();

    // Stage the binary the way a platform package or a previous run would
    // have, so the full npm install runs hermetically, without a download.
    let staged = pkg.join("bin/tai");
    fs::copy(built_binary(), &staged).unwrap();
    fs::set_permissions(&staged, fs::Permissions::from_mode(0o755)).unwrap();

    let path = std::env::var("PATH").unwrap_or_default();
    let got = Command::new("bash")
        .arg(pkg.join("bin/npm-postinstall"))
        .env("HOME", &home)
        .env("XDG_DATA_HOME", pack_dir.join("data_full"))
        .env("npm_config_global", "1")
        .env("BIN_DIR", &bin_dir)
        .env("PATH", format!("{}:{}", pkg.join("bin").display(), path))
        .env("ZDOTDIR", &home)
        .output()
        .expect("failed to run npm-postinstall (full)");
    assert_eq!(got.status.code(), Some(0), "the postinstall exits 0");
    let out = String::from_utf8_lossy(&got.stdout);
    assert!(out.contains("✓ zsh installed"), "the install ran");

    let zshrc = fs::read_to_string(home.join(".zshrc")).unwrap();
    assert!(
        zshrc.replace("\\ ", " ").contains(&format!("{}/plugins/tai.zsh", pkg.display()))
            || zshrc.contains("plugins/tai.zsh"),
        "the rc points into the package"
    );
    let method = pack_dir.join("data_full/tai/install-method");
    assert!(
        method.exists()
            && fs::read_to_string(&method).unwrap().trim() == "npm",
        "the npm door is recorded"
    );

    let got = Command::new(bin_dir.join("tai"))
        .arg("version")
        .env("HOME", &home)
        .env("XDG_DATA_HOME", pack_dir.join("data_full"))
        .output()
        .expect("failed to run the installed wrapper");
    let _ = std::io::stdout().flush();
    assert!(
        String::from_utf8_lossy(&got.stdout).starts_with(&format!("tai {}", version())),
        "the wrapper answers"
    );
}

/// The tests run in parallel over one packed package, and the runs below
/// mutate it (they stage and fetch a binary into bin/). Each takes its own
/// copy of the unpacked package so the runs cannot write under each other,
/// and the copy carries no binary another run may already have staged — the
/// fetch and the full install must both meet the package as npm ships it.
fn own_pkg_copy(pack_dir: &Path, name: &str) -> PathBuf {
    let src = pack_dir.join("pkg/package");
    let dst = pack_dir.join(format!("pkg_{name}/package"));
    copy_tree(&src, &dst);
    let _ = fs::remove_file(dst.join("bin/tai"));
    let _ = fs::remove_dir_all(dst.join("bin/tai-linux-x86_64"));
    let _ = fs::remove_dir_all(dst.join("bin/tai-linux-aarch64"));
    dst
}

fn copy_tree(src: &Path, dst: &Path) {
    fs::create_dir_all(dst).unwrap();
    for entry in fs::read_dir(src).unwrap() {
        let entry = entry.unwrap();
        let to = dst.join(entry.file_name());
        if entry.file_type().unwrap().is_dir() {
            copy_tree(&entry.path(), &to);
        } else {
            fs::copy(entry.path(), &to).unwrap();
        }
    }
}

/// The launcher npm links: with the binary staged in the package, `tai` via
/// bin/tai.js answers `tai <version>` — the command a scripts-blocked install
/// runs the first time, before any hook has ever helped it.
#[test]
fn launcher_runs_the_binary() {
    let pack_dir = prepare();
    let pkg = own_pkg_copy(pack_dir, "launcher");
    let home = pack_dir.join("home_launcher");
    fs::create_dir_all(&home).unwrap();

    let staged = pkg.join("bin/tai");
    fs::copy(built_binary(), &staged).unwrap();
    fs::set_permissions(&staged, fs::Permissions::from_mode(0o755)).unwrap();

    let got = Command::new("node")
        .arg(pkg.join("bin/tai.js"))
        .arg("version")
        .env("HOME", &home)
        .env("XDG_DATA_HOME", pack_dir.join("data_launcher"))
        .output()
        .expect("failed to run the launcher");
    assert_eq!(got.status.code(), Some(0), "the launcher exits 0");
    assert!(
        String::from_utf8_lossy(&got.stdout).starts_with(&format!("tai {}", version())),
        "the launcher hands the terminal to the binary"
    );
}

/// A one-file HTTP server for the hermetic fetch: GET /tai-linux-<arch>.tar.gz
/// answers with the bytes of a tarball built here in the exact shape the
/// GitHub release ships — a tai-linux-<arch> directory carrying the binary —
/// and everything else with 404.
fn serve_tarball(tarball: &Path) -> (std::net::TcpListener, std::thread::JoinHandle<()>) {
    use std::io::Read;
    let listener = std::net::TcpListener::bind("127.0.0.1:0").unwrap();
    let handle = std::thread::spawn({
        let tarball = tarball.to_path_buf();
        let listener = listener.try_clone().expect("failed to clone the listener socket");
        move || {
            let mut body = Vec::new();
            if fs::File::open(&tarball).and_then(|mut f| f.read_to_end(&mut body)).is_err() {
                return;
            }
            for stream in listener.incoming() {
                let Ok(mut stream) = stream else { break };
                // The request head is all curl waits to be read — the client
                // holds its write side open for the answer, so reading to EOF
                // would deadlock. Read to the blank line, then respond.
                let mut buf = Vec::new();
                let mut chunk = [0u8; 1024];
                loop {
                    match stream.read(&mut chunk) {
                        Ok(0) => break,
                        Ok(n) => {
                            buf.extend_from_slice(&chunk[..n]);
                            if buf.windows(4).any(|w| w == b"\r\n\r\n") {
                                break;
                            }
                        }
                        Err(_) => break,
                    }
                }
                let request = String::from_utf8_lossy(&buf).to_string();
                let want = request
                    .lines()
                    .next()
                    .unwrap_or("")
                    .split_whitespace()
                    .nth(1)
                    .unwrap_or("")
                    .to_string();
                if want.ends_with(".tar.gz") {
                    let _ = stream.write_all(
                        format!(
                            "HTTP/1.0 200 OK\r\nContent-Length: {}\r\nConnection: close\r\n\r\n",
                            body.len()
                        )
                        .as_bytes(),
                    );
                    let _ = stream.write_all(&body);
                } else {
                    let _ = stream.write_all(
                        b"HTTP/1.0 404 Not Found\r\nContent-Length: 0\r\nConnection: close\r\n\r\n",
                    );
                }
            }
        }
    });
    (listener, handle)
}

/// The download path, end to end and hermetically: the postinstall aimed at a
/// local server carrying a release-shaped tarball stages bin/tai out of the
/// tai-linux-<arch>/ directory the tarball actually unpacks, installs it, and
/// wires both shells — the run npm's `--allow-scripts` door performs.
#[test]
fn postinstall_fetches_the_release_tarball() {
    let pack_dir = prepare();
    let pkg = own_pkg_copy(pack_dir, "fetch");
    let home = pack_dir.join("home_fetch");

    // A tarball in the shape the release workflow ships.
    let rel = pack_dir.join("rel");
    let arch_dir = match std::env::consts::ARCH {
        "aarch64" => "aarch64".to_string(),
        _ => "x86_64".to_string(),
    };
    let stage = rel.join(format!("tai-linux-{arch_dir}"));
    fs::create_dir_all(&stage).unwrap();
    fs::copy(built_binary(), stage.join("tai")).unwrap();
    fs::set_permissions(stage.join("tai"), fs::Permissions::from_mode(0o755)).unwrap();
    let tarball = rel.join(format!("{arch_dir}.tar.gz"));
    let tar = Command::new("tar")
        .args(["-czf"])
        .arg(&tarball)
        .arg(format!("tai-linux-{arch_dir}"))
        .current_dir(&rel)
        .output()
        .expect("failed to run tar");
    assert!(
        tar.status.success(),
        "the fake release tarball builds: {}",
        String::from_utf8_lossy(&tar.stderr)
    );

    let (listener, _server) = serve_tarball(&tarball);
    let port = listener.local_addr().unwrap().port();

    let path = std::env::var("PATH").unwrap_or_default();
    let got = Command::new("bash")
        .arg(pkg.join("bin/npm-postinstall"))
        .env("HOME", &home)
        .env("XDG_DATA_HOME", pack_dir.join("data_fetch"))
        .env("npm_config_global", "1")
        .env("TAI_RELEASE_URL", format!("http://127.0.0.1:{port}/{arch_dir}.tar.gz"))
        .env("BIN_DIR", home.join(".local/bin"))
        .env("PATH", format!("{}:{}", pkg.join("bin").display(), path))
        .env("ZDOTDIR", &home)
        .output()
        .expect("failed to run npm-postinstall (fetch)");
    assert_eq!(got.status.code(), Some(0), "the postinstall exits 0");
    let out = String::from_utf8_lossy(&got.stdout);
    assert!(
        out.contains("fetching the binary"),
        "the fetch ran: {out}"
    );
    assert!(out.contains("✓ zsh installed"), "the fetched binary finished the install: {out}");
    assert!(pkg.join("bin/tai").is_file(), "the fetched binary is staged in the package");
    assert!(
        !pkg.join(format!("bin/{arch_dir}")).is_dir(),
        "the tarball's directory does not linger as a second tai"
    );

    let method = pack_dir.join("data_fetch/tai/install-method");
    assert!(
        method.exists() && fs::read_to_string(&method).unwrap().trim() == "npm",
        "the npm door is recorded"
    );
}
