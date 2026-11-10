//! The .deb: what it carries, and that it is the compiled program.
//!
//!     cargo test --release --test pkg_deb
//!
//! `scripts/build_deb.sh` compiles the release binary and packages it with the
//! shell plugins. The package is architecture-keyed now, not "all": the binary
//! inside is the machine this built on. dpkg-deb is the floor; without it the
//! suite says so and skips, the way the pty suite treats a missing zsh.
//!
//! Ported from tests/test_deb.py.

use std::fs;
use std::path::PathBuf;
use std::process::Command;

fn repo() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR"))
}

fn version() -> String {
    fs::read_to_string(repo().join("VERSION")).unwrap().trim().to_string()
}

fn which(cmd: &str) -> bool {
    match std::env::var_os("PATH") {
        Some(path) => std::env::split_paths(&path)
            .map(|dir| dir.join(cmd))
            .any(|p| p.is_file()),
        None => false,
    }
}

/// Python's str.split(maxsplit=5): at most five splits on runs of whitespace,
/// so the sixth field keeps any spaces a filename might carry.
fn split_maxsplit5(line: &str) -> Vec<&str> {
    let mut parts = Vec::new();
    let mut rest = line;
    for _ in 0..5 {
        rest = rest.trim_start();
        match rest.find(char::is_whitespace) {
            Some(i) => {
                parts.push(&rest[..i]);
                rest = &rest[i..];
            }
            None => break,
        }
    }
    let rest = rest.trim_start();
    if !rest.is_empty() {
        parts.push(rest);
    }
    parts
}

#[test]
fn deb_build_and_layout() {
    if !which("dpkg-deb") {
        eprintln!("skipping: dpkg-deb is not installed — the deb checks are skipped");
        return;
    }

    let got = Command::new("bash")
        .arg(repo().join("scripts/build_deb.sh"))
        .current_dir(repo())
        .output()
        .expect("failed to run build_deb.sh");
    assert_eq!(
        got.status.code(),
        Some(0),
        "build exits 0\nstdout:\n{}\nstderr:\n{}",
        String::from_utf8_lossy(&got.stdout),
        String::from_utf8_lossy(&got.stderr)
    );
    let arch = String::from_utf8_lossy(
        &Command::new("dpkg")
            .args(["--print-architecture"])
            .output()
            .expect("failed to run dpkg --print-architecture")
            .stdout,
    )
    .trim()
    .to_string();
    let arch = if arch.is_empty() { "amd64".to_string() } else { arch };
    let deb = repo().join(format!("dist/tai_{}_{}.deb", version(), arch));
    assert!(
        deb.exists(),
        "the deb is named for version and architecture"
    );

    let listing = String::from_utf8_lossy(
        &Command::new("dpkg-deb")
            .args(["-c"])
            .arg(&deb)
            .output()
            .expect("failed to run dpkg-deb -c")
            .stdout,
    )
    .into_owned();
    let paths: Vec<String> = listing
        .lines()
        .filter(|l| !l.trim().is_empty())
        .map(|line| {
            let last = split_maxsplit5(line)
                .last()
                .copied()
                .unwrap_or("");
            last.strip_prefix("./")
                .unwrap_or(last)
                .to_string()
        })
        .collect();
    assert!(paths.iter().any(|p| p == "usr/bin/tai"), "the binary is /usr/bin/tai");
    assert!(
        paths.iter().any(|p| p == "usr/lib/tai/plugins/tai.zsh"),
        "the zsh plugin ships"
    );
    assert!(
        paths.iter().any(|p| p == "usr/lib/tai/plugins/tai.bash"),
        "the bash plugin ships"
    );
    assert!(
        !paths.iter().any(|p| p.ends_with(".py")),
        "no Python ships"
    );

    let control = String::from_utf8_lossy(
        &Command::new("dpkg-deb")
            .args(["-f"])
            .arg(&deb)
            .arg("Architecture")
            .output()
            .expect("failed to run dpkg-deb -f Architecture")
            .stdout,
    )
    .trim()
    .to_string();
    assert_eq!(control, arch, "architecture is the build machine's");
    let depends = String::from_utf8_lossy(
        &Command::new("dpkg-deb")
            .args(["-f"])
            .arg(&deb)
            .arg("Depends")
            .output()
            .expect("failed to run dpkg-deb -f Depends")
            .stdout,
    )
    .into_owned();
    assert!(!depends.contains("python3"), "no python3 dependency");

    // The binary inside runs, and says the release it is.
    let extract = PathBuf::from(format!("/tmp/tai/tai_deb_{}", version()));
    let _ = fs::remove_dir_all(&extract);
    fs::create_dir_all(&extract).unwrap();
    let st = Command::new("dpkg-deb")
        .args(["-x"])
        .arg(&deb)
        .arg(&extract)
        .status()
        .expect("failed to run dpkg-deb -x");
    assert!(st.success(), "dpkg-deb -x extracted the package");
    let got = Command::new(extract.join("usr/bin/tai"))
        .arg("version")
        .env_clear()
        .env("HOME", &extract)
        .output()
        .expect("failed to run the packaged binary");
    assert!(
        String::from_utf8_lossy(&got.stdout)
            .starts_with(&format!("tai {}", version())),
        "the packaged binary answers"
    );
    let _ = fs::remove_dir_all(&extract);
}
