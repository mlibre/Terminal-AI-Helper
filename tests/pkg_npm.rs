//! The npm door: the package npm ships, and what its postinstall does.
//!
//!     cargo test --release --test pkg_npm
//!
//! The registry package is architecture-neutral: plugins, installer, version —
//! and the postinstall fetches or builds the binary. Both facts are checked
//! here against a real `npm pack`, and the hook is run twice: once with the
//! scripts skip (containers want files on disk only), once with the binary
//! pre-staged — the way a platform package or a previous run would have left
//! it — so the full npm install runs hermetically, without a download.
//!
//! node and npm are the floor this suite stands on; without them it says so
//! and skips, the way the pty suite treats a missing zsh.
//!
//! Ported from tests/test_npm.py. The tarball is inspected with the system
//! `tar` (Python used the tarfile module). The three tests share one packed
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
    let pack_dir = prepare();
    let pkg = pack_dir.join("pkg/package");
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
