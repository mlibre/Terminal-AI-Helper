//! tai — the terminal that knows your next command.
//!
//! One binary: the store, the engine, the index builder, the discovery, the
//! diagnostics and the dashboard. The shell plugins read the indexes it
//! writes and answer every keystroke themselves — no daemon, no socket, no
//! process on the keystroke path.

mod bench;
mod cli;
mod dbg;
mod decisions;
mod engcache;
mod engine;
mod evaluate;
mod evaluate_jev;
mod fresh;
mod helptext;
mod index;
mod jev;
mod knowledge;
mod maintenance;
mod paths;
mod predictor;
mod seed;
mod spool;
mod store;
mod tune;
mod typo;
mod util;
mod web;

fn main() {
    // The panic hook before anything that can panic: a tai that dies with a
    // Rust panic leaves the payload on the debug trail, beside whatever the
    // shell wrote about the same second.
    dbg::install_panic_hook();
    let argv: Vec<String> = std::env::args().skip(1).collect();
    let code = cli::run(&argv);
    std::process::exit(code);
}
