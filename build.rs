use std::env;
use std::fs;
use std::path::Path;

fn main() {
    // The release this binary is. Read from the VERSION file beside the crate
    // at compile time, so `tai version` answers without a file beside the
    // binary: a v3 install is one file, and one file carries its own name.
    let manifest = env::var("CARGO_MANIFEST_DIR").unwrap_or_default();
    let version = fs::read_to_string(Path::new(&manifest).join("VERSION"))
        .unwrap_or_else(|_| "0.0.0".to_string())
        .trim()
        .to_string();
    println!("cargo:rustc-env=TAI_VERSION={}", version);
    println!("cargo:rerun-if-changed=VERSION");
}
